"""把 LangGraph 的 astream 翻译成「给用户看的阶段日志」。

设计原则：**前端只要知道"谁在干什么"，不需要看到每一个 token。**
所以这里把流压缩成少量 step 事件：

    {"type":"step","id":"s3","kind":"tool","agent":"出行规划师",
     "label":"调用 get-tickets","status":"running"}
    {"type":"step","id":"s3","status":"ok","ms":2400}

kind 有四种：
    agent   某个智能体开始干活（正在思考/输出）
    tool    调用一个工具
    handoff 项目经理把活派给某个专家
    phase   阶段切换（用户确认、落盘）

额外还有 interrupt / result / error 三种事件。

和上一版最大的区别：**不再推 token**。理由很直接 —— 逐字输出看起来热闹，
但用户真正想知道的是"现在走到哪一步了"，日志比打字机更有用。
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

# MCP server 会给工具名加前缀（amap_maps_text_search、rail12306_get-tickets）
_PREFIXES = ("amap_", "rail12306_", "budget_", "mcp_")

# 工具名 → 人话。没映射到的就直接显示工具名。
_TOOL_LABELS = {
    "write_todos": "更新任务计划",
    "read_file": "读取文档",
    "write_file": "写入文件",
    "edit_file": "修改文件",
    "ls": "浏览文件",
    "confirm_itinerary": "请求用户确认",
    "book_hold": "模拟锁单",
    "calculate_budget": "核算预算",
    "maps_text_search": "搜索景点",
    "maps_around_search": "搜索周边",
    "maps_search_detail": "查景点详情",
    "maps_weather": "查天气",
    "maps_geo": "地址转坐标",
    "maps_distance": "算距离",
    "maps_direction_transit_integrated": "规划公交路线",
    "maps_schema_personal_map": "生成行程地图",
    "get-tickets": "查询车次余票",
    "get-interline-tickets": "查询中转方案",
    "get-stations-code-in-city": "查车站代码",
    "get-current-date": "确认今天日期",
    "estimate_flight": "估算机票",
}


# --------------------------------------------------------------------------- #
# 基础工具（保留纯函数，方便离线单测）
# --------------------------------------------------------------------------- #
def short_tool_name(name: str) -> str:
    """去掉 MCP server 加的命名前缀。"""
    for prefix in _PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def text_of(message: Any) -> str:
    """从消息对象里取纯文本。"""
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def agent_label(namespace: Any, metadata: dict | None) -> str:
    """判断这条流是哪个智能体产生的。"""
    name = (metadata or {}).get("lc_agent_name")
    if name:
        return str(name)
    if not namespace:
        return "项目经理"
    return "子智能体"


def _brief(value: Any, limit: int = 40) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


# --------------------------------------------------------------------------- #
# updates → 「动作」列表（还没分配 id，由上面的生成器负责计时和配对）
# --------------------------------------------------------------------------- #
def interrupt_items(data: Any) -> list[dict]:
    """解析 HITL 中断，整理成前端画卡片需要的结构。"""
    items: list[dict] = []
    for interrupt in (data or {}).get("__interrupt__") or []:
        value = getattr(interrupt, "value", None) or {}
        review = {
            cfg.get("action_name"): cfg
            for cfg in (value.get("review_configs") or [])
            if isinstance(cfg, dict)
        }
        actions = []
        for action in value.get("action_requests") or []:
            name = action.get("name", "?")
            actions.append(
                {
                    "name": short_tool_name(str(name)),
                    "raw_name": str(name),
                    "args": action.get("args") or {},
                    "allowed": (review.get(name) or {}).get(
                        "allowed_decisions", ["approve", "reject"]
                    ),
                }
            )
        items.append({"interrupt_id": getattr(interrupt, "id", None), "actions": actions})
    return items


def steps_from_update(data: Any, label: str) -> list[dict]:
    """把一次 updates 流的数据翻成待编号的动作。

    open  : {"_action":"open","key":<调用id>,"kind":...,"agent":...,"label":...}
    close : {"_action":"close","key":<调用id>,"ok":bool}
    """
    actions: list[dict] = []
    if not isinstance(data, dict):
        return actions

    for node, payload in data.items():
        if node == "__interrupt__":
            continue
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not messages:
            continue

        for message in messages or []:
            # ① AI 消息里带的工具调用
            for call in getattr(message, "tool_calls", None) or []:
                if not isinstance(call, dict):
                    continue
                raw_name = str(call.get("name", ""))
                name = short_tool_name(raw_name)
                args = call.get("args") or {}

                if name == "task":
                    target = args.get("subagent_type") or args.get("name") or "专家"
                    actions.append(
                        {
                            "_action": "open",
                            "key": call.get("id") or raw_name,
                            "kind": "handoff",
                            "agent": label,
                            "label": f"派活给 {_brief(target, 24)}",
                        }
                    )
                else:
                    actions.append(
                        {
                            "_action": "open",
                            "key": call.get("id") or raw_name,
                            "kind": "tool",
                            "agent": label,
                            "label": _TOOL_LABELS.get(name, f"调用 {name}"),
                        }
                    )

            # ② 工具返回
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id:
                actions.append(
                    {
                        "_action": "close",
                        "key": tool_call_id,
                        "ok": getattr(message, "status", "success") != "error",
                    }
                )

    return actions


# --------------------------------------------------------------------------- #
# 主生成器
# --------------------------------------------------------------------------- #
async def stream_events(agent: Any, payload: Any, cfg: dict) -> AsyncIterator[dict]:
    """跑一次 agent，把过程压成阶段日志事件。"""
    seq = 0
    open_steps: dict[str, dict] = {}  # key → {"id","t0"}
    output_steps: dict[str, str] = {}  # agent → step id（"正在思考"那种）
    # namespace → 智能体名字。updates 流里拿不到 lc_agent_name，
    # 只能先在 messages 流里记下来，再按 namespace 反查，否则全都显示成"专家"。
    ns_names: dict[tuple, str] = {}

    def new_id() -> str:
        nonlocal seq
        seq += 1
        return f"s{seq}"

    try:
        async for item in agent.astream(
            payload,
            config=cfg,
            stream_mode=["messages", "updates"],
            subgraphs=True,
        ):
            if not (isinstance(item, tuple) and len(item) == 3):
                continue
            namespace, mode, data = item
            ns_key = tuple(namespace or ())

            # ---------- ① 文本增量：只用来判断"某个智能体开始干活了" ----------
            if mode == "messages":
                chunk, metadata = data
                if not type(chunk).__name__.endswith("AIMessageChunk"):
                    continue
                if not text_of(chunk).strip():
                    continue  # 只有 tool_calls、没有正文的增量，忽略

                label = agent_label(namespace, metadata)
                name = (metadata or {}).get("lc_agent_name")
                if name:
                    ns_names[ns_key] = str(name)

                if label not in output_steps:
                    step_id = new_id()
                    output_steps[label] = step_id
                    yield {
                        "type": "step",
                        "id": step_id,
                        "kind": "agent",
                        "agent": label,
                        "label": "正在思考",
                        "status": "running",
                    }

            # ---------- ② 节点更新：工具调用与返回 ----------
            elif mode == "updates":
                label = ns_names.get(ns_key) or agent_label(namespace, None)

                # HITL 暂停：先把"正在思考"收掉，再抛中断事件
                if isinstance(data, dict) and "__interrupt__" in data:
                    finished = output_steps.pop(label, None)
                    if finished:
                        yield {"type": "step", "id": finished, "status": "ok"}
                    yield {"type": "interrupt", "items": interrupt_items(data)}
                    continue

                # 这个 agent 开始调工具了，说明"正在思考"结束
                finished = output_steps.pop(label, None)
                if finished:
                    yield {"type": "step", "id": finished, "status": "ok"}

                for action in steps_from_update(data, label):
                    key = action["key"]
                    if action["_action"] == "open":
                        step_id = new_id()
                        open_steps[key] = {"id": step_id, "t0": time.perf_counter()}
                        yield {
                            "type": "step",
                            "id": step_id,
                            "kind": action["kind"],
                            "agent": action["agent"],
                            "label": action["label"],
                            "status": "running",
                        }
                    else:  # close
                        opened = open_steps.pop(key, None)
                        if not opened:
                            continue  # 结果先到 / 没有对应调用，忽略
                        elapsed = int((time.perf_counter() - opened["t0"]) * 1000)
                        yield {
                            "type": "step",
                            "id": opened["id"],
                            "status": "ok" if action["ok"] else "error",
                            "ms": elapsed,
                        }

    except Exception as exc:  # noqa: BLE001 - 前端要看到错误，而不是白屏
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
        return

    # 收尾：把还挂着的 running 关掉，避免界面一直转圈
    for opened in list(open_steps.values()):
        yield {"type": "step", "id": opened["id"], "status": "ok"}
    for step_id in output_steps.values():
        yield {"type": "step", "id": step_id, "status": "ok"}


async def final_state_message(agent: Any, cfg: dict) -> str:
    """流结束后取最后一条 AI 文本，用于落盘和最终结果展示。"""
    try:
        state = await agent.aget_state(cfg)
        messages = (state.values or {}).get("messages") or []
        for message in reversed(messages):
            if getattr(message, "type", "") == "ai":
                text = text_of(message)
                if text.strip():
                    return text
    except Exception:  # noqa: BLE001
        pass
    return ""


async def final_proposal(agent: Any, cfg: dict) -> str | None:
    """从最终状态里找出 publish_proposal 的落盘文件名。

    返回 None 表示这一轮只是追问/闲聊，没有真的产出策划书 ——
    前端因此不会给出下载链接（避免把一句「请告诉我目的地」存成策划书）。
    """
    try:
        state = await agent.aget_state(cfg)
        messages = (state.values or {}).get("messages") or []
    except Exception:  # noqa: BLE001
        return None

    for message in reversed(messages):
        if getattr(message, "name", "") != "publish_proposal":
            continue
        try:
            payload = json.loads(text_of(message))
        except (ValueError, TypeError):
            continue
        if payload.get("ok") and payload.get("file"):
            return str(payload["file"])
    return None


def dumps(event: dict) -> str:
    """一条事件 = 一行 NDJSON。"""
    return json.dumps(event, ensure_ascii=False) + "\n"
