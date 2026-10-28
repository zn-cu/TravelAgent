"""预算工具的核心逻辑（纯标准库，零依赖）。

这是整个项目里唯一自研的工具，也是唯一一个你能完整看懂、随意改的 MCP server。
把它单独放一个文件，是因为它同时被三处复用：

    mcp_server/server.py        暴露成 MCP 工具（Agent 走这条路）
    tests/test_budget.py        单元测试
    check_env.py                环境自检

设计约束（为什么这么写）：
1. 返回 JSON 字符串，而不是自然语言 —— LLM 读结构化结果更不容易幻觉。
2. 失败也返回 JSON `{"ok": false, "error": ...}`，不抛异常 —— 抛异常会打断整条 Agent 链路，
   而 Agent 收到 ok=false 后可以自我纠错（比如换个格式重试）。
3. 解析要宽容：中文/英文标点、全角冒号、`金额*数量` 都支持；解析不了就进 warnings 而不是报错。
"""

from __future__ import annotations

import json
import re
from typing import Any

# 「机票:500」「机票：500」「机票:500*2」「机票 500」
# 说明：冒号后允许「元」，数量之前允许 * x × X
_ITEM_RE = re.compile(
    r"^(?P<name>[^:：]+?)\s*[:：]\s*(?P<amount>\d+(?:\.\d+)?)\s*元?"
    r"(?:\s*[*x×X]\s*(?P<qty>\d+))?$"
)
_SPACE_ITEM_RE = re.compile(r"^(?P<name>\D+?)\s+(?P<amount>\d+(?:\.\d+)?)\s*元?$")


def _parse_items(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """把「机票:500,门票:200*2」解析成结构化条目。

    返回 (条目列表, 警告列表)。解析不了的片段进警告，不抛异常。
    """
    items: list[dict[str, Any]] = []
    warnings: list[str] = []

    for raw in re.split(r"[，,;；、\n]+", text):
        token = raw.strip()
        if not token:
            continue

        match = _ITEM_RE.match(token) or _SPACE_ITEM_RE.match(token)
        if not match:
            warnings.append(f"无法解析「{token}」，已跳过（格式示例：机票:500,门票:200）")
            continue

        name = match.group("name").strip()
        amount = float(match.group("amount"))
        quantity = int(match.groupdict().get("qty") or 1)
        items.append(
            {
                "name": name,
                "unit_price": round(amount, 2),
                "quantity": quantity,
                "subtotal": round(amount * quantity, 2),
            }
        )

    return items, warnings


def calculate_budget(items: str) -> str:
    """解析消费项目并核算总金额，例如「机票:500,门票:200」。"""
    # 有些模型会把清单塞成数组，这里兼容一下
    if isinstance(items, (list, tuple)):
        text = ",".join(str(x) for x in items)
    else:
        text = str(items or "")

    parsed, warnings = _parse_items(text)
    if not parsed:
        return _dumps(
            {
                "ok": False,
                "error": "没有解析到任何消费项目，请使用「项目:金额」格式，例如：机票:500,门票:200",
                "warnings": warnings,
            }
        )

    total = round(sum(item["subtotal"] for item in parsed), 2)
    buffer_ = round(total * 0.10, 2)
    return _dumps(
        {
            "ok": True,
            "tool": "calculate_budget",
            "currency": "CNY",
            "items": parsed,
            "item_count": len(parsed),
            "total": total,
            "suggested_buffer": buffer_,
            "grand_total": round(total + buffer_, 2),
            "warnings": warnings,
            "note": "建议预留 10% 机动金（suggested_buffer）应对临时改签、加餐等。",
        }
    )


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
