"""命令行入口：多轮对话 + HITL 中断恢复 + 策划书落盘。

用法：
    python -m travel_agent.cli "我想去成都玩3天"
    python -m travel_agent.cli                    # 进入交互模式
    python -m travel_agent.cli --list-tools       # 只检查 MCP 连不连得上

HITL 的核心其实就是 `run_turn` 里那个 while 循环：
    调一次 → 有 __interrupt__ 就收集用户决策 → 用 Command(resume=...) 再调一次。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import textwrap
import uuid

from langgraph.types import Command

from . import config
from .agent import build_ceo_agent
from .mcp_gateway import open_toolbox
from .streaming import final_proposal


def force_utf8() -> None:
    """Windows 控制台默认 GBK，中文/箭头会抛 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


# --------------------------------------------------------------------------- #
# HITL：中断渲染 + 决策收集
# --------------------------------------------------------------------------- #
def render_card(index: int, total: int, action: dict, allowed: list[str]) -> None:
    """把中断渲染成人话。直接丢 JSON 给用户看是没用的。"""
    print("\n" + "─" * 62)
    print(f"  待确认 {index}/{total}   工具：{action.get('name')}")
    print("─" * 62)

    args = action.get("args", {})
    for key, value in args.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        wrapped = textwrap.wrap(str(text), width=56) or [""]
        print(f"  {key}：{wrapped[0]}")
        for line in wrapped[1:]:
            print(f"  {' ' * (len(key) + 3)}{line}")

    print(f"  可选：{' / '.join(allowed)}")
    print("─" * 62)


def collect_decisions(interrupt_value: dict) -> list[dict]:
    """为这一批待确认动作逐个收集决策。顺序必须和 action_requests 一致。"""
    actions = interrupt_value.get("action_requests", [])
    review = {cfg.get("action_name"): cfg for cfg in interrupt_value.get("review_configs", [])}

    decisions: list[dict] = []
    for index, action in enumerate(actions, start=1):
        name = action.get("name", "?")
        allowed = review.get(name, {}).get("allowed_decisions", ["approve", "reject"])
        render_card(index, len(actions), action, allowed)

        while True:
            choice = input("  你的决定 [a]pprove / [e]dit / [r]eject > ").strip().lower() or "a"

            if choice.startswith("a"):
                decisions.append({"type": "approve"})
                break

            if choice.startswith("e") and "edit" in allowed:
                raw = input("  新的参数（JSON，直接回车取消）> ").strip()
                if not raw:
                    continue
                try:
                    new_args = json.loads(raw)
                except json.JSONDecodeError as exc:
                    print(f"  JSON 格式不对：{exc}")
                    continue
                decisions.append(
                    {"type": "edit", "edited_action": {"name": name, "args": new_args}}
                )
                break

            if choice.startswith("r") and "reject" in allowed:
                reason = input("  否决理由（会原样转告模型）> ").strip()
                decisions.append(
                    {
                        "type": "reject",
                        "message": reason
                        or "用户否决了这一步。不要重试同样的操作，先问清需求再继续。",
                    }
                )
                break

            print(f"  无效输入（这一项允许：{', '.join(allowed)}）")

    return decisions


def build_resume(interrupts: list) -> Command:
    """单个中断用扁平 payload；多个中断要按 interrupt.id 分别映射。"""
    if len(interrupts) == 1:
        return Command(resume={"decisions": collect_decisions(interrupts[0].value)})
    return Command(
        resume={item.id: {"decisions": collect_decisions(item.value)} for item in interrupts}
    )


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def last_text(result: dict) -> str:
    """取最后一条 AI 消息的纯文本。"""
    messages = result.get("messages") or []
    if not messages:
        return "（没有返回任何内容）"
    content = messages[-1].content
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts) or str(content)


async def run_turn(agent, payload, cfg) -> dict:
    """一轮对话：反复「调 agent → 有中断就问人 → 带上决策重调」，直到跑完。"""
    while True:
        result = await agent.ainvoke(payload, config=cfg)
        interrupts = result.get("__interrupt__")
        if not interrupts:
            return result
        payload = build_resume(list(interrupts))


async def main_async(args) -> int:
    force_utf8()

    problems = config.check_env()
    for problem in problems:
        print(f"⚠ {problem}")
    if problems and not args.list_tools:
        print("（继续运行也可以，但上面这些问题会导致某些专家没工具）\n")

    async with open_toolbox(timeout=args.timeout, skip=args.skip) as box:
        if args.list_tools:
            print("\n--list-tools 结束，没有启动 Agent。")
            return 0

        agent = build_ceo_agent(box)
        thread = args.thread or f"travel-{uuid.uuid4().hex[:6]}"
        cfg = {"configurable": {"thread_id": thread}}
        print(f"\n会话 id：{thread}（HITL 恢复必须用同一个 id）")

        text = args.query or input("\n你想去哪玩？> ").strip()
        while text:
            print("\n" + "=" * 62)
            result = await run_turn(agent, {"messages": [{"role": "user", "content": text}]}, cfg)
            answer = last_text(result)
            file = await final_proposal(agent, cfg)

            print("\n" + answer)
            if file:
                print(f"\n策划书已生成：{config.OUTPUT_DIR / file}")
            else:
                print("\n（本轮没有生成策划书——只是回复或追问）")

            try:
                text = input("\n继续提问（直接回车退出）> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="智能旅游规划 Agent")
    parser.add_argument("query", nargs="?", help="你的需求，例如：我想去成都玩3天")
    parser.add_argument("--thread", help="会话 id（想恢复上次中断时用）")
    parser.add_argument("--list-tools", action="store_true", help="只检查 MCP 工具，不启动 Agent")
    parser.add_argument(
        "--skip", action="append", default=None, help="临时跳过某个 MCP server，可重复：--skip amap"
    )
    parser.add_argument("--timeout", type=float, help="单个 MCP server 的连接超时（秒）")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
