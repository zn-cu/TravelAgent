"""两个自定义中间件：调用日志 + 限流。

中间件和工具的区别：
    工具是「能力」，中间件是「纪律」。
    限流、日志这类横切关注点写进中间件，就不用去改第三方 MCP 的代码。

两件事必须注意：
1. 带参数的中间件只能写成**类**（继承 AgentMiddleware）。函数式 `@wrap_tool_call`
   装饰器拿不到构造参数，只能写死逻辑。
2. 本项目全程走异步（`await agent.ainvoke(...)`，因为 MCP 工具本身是异步的），
   所以只实现异步钩子 `awrap_tool_call`。如果你改用同步 `invoke`，
   需要再补一个同步版 `wrap_tool_call`，否则中间件不生效。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from . import config


def _tool_message(request: Any, text: str):
    """构造一条「工具没真跑，但结果是这句话」的消息。

    比直接抛异常温柔：抛异常会让 Agent 中断或者疯狂重试，
    而返回一条正常的工具结果，模型会自己想办法绕过去。
    """
    from langchain.messages import ToolMessage

    tool_call = getattr(request, "tool_call", {}) or {}
    return ToolMessage(content=text, tool_call_id=tool_call.get("id", ""))


def _brief(args: Any, limit: int = 60) -> str:
    """把参数压成一行短文本，日志里好看。"""
    try:
        text = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(args)
    return text if len(text) <= limit else text[:limit] + "…"


class TraceMiddleware(AgentMiddleware):
    """每次工具调用打印一行，并追加到 logs/trace.jsonl。

    输出示例：
        [14:22:31] maps_text_search({"keywords": "成都 景点"}) 1.2s ok
    调 Agent 的时候，先看这个就知道它到底在调什么、卡在哪。
    """

    def __init__(self, trace_file=None) -> None:
        super().__init__()
        self.trace_file = trace_file or config.TRACE_FILE
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)

    async def awrap_tool_call(self, request, handler):
        name = getattr(request, "name", "?")
        args = getattr(request, "args", {}) or {}
        started = time.perf_counter()
        status = "ok"
        try:
            return await handler(request)
        except Exception as exc:
            status = f"error:{type(exc).__name__}"
            raise
        finally:
            elapsed = time.perf_counter() - started
            print(f"  [{time.strftime('%H:%M:%S')}] {name}({_brief(args)}) {elapsed:.1f}s {status}",
                  flush=True)
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "tool": name,
                "args": args if isinstance(args, dict) else str(args),
                "elapsed": round(elapsed, 3),
                "status": status,
            }
            # 追加写，一行一条 JSON，方便之后 `type logs\trace.jsonl` 或 jq 过滤
            with self.trace_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class RateLimitMiddleware(AgentMiddleware):
    """给 12306 的查询工具限流。

    为什么必须限流：12306 是第三方非官方接口，高频查询会被风控
    （表现为空结果或直接拒绝），限流是保护自己也是保护对方。

    规则：
    - 两次查询至少间隔 min_interval 秒（默认 3 秒）
    - 单会话最多 max_calls 次（默认 20 次）
    - 某次查询报错后，间隔指数退避（3 → 6 → 12 秒，最多 30 秒）
    - 超过上限时不抛异常，而是返回一句人话，让 Agent 用已有结果收尾
    """

    def __init__(
        self,
        tool_pattern: str = r"get-tickets|get_tickets|get-interline-tickets",
        min_interval: float | None = None,
        max_calls: int | None = None,
    ) -> None:
        super().__init__()
        self.pattern = re.compile(tool_pattern)
        self.min_interval = config.RAIL_MIN_INTERVAL if min_interval is None else min_interval
        self.max_calls = config.RAIL_MAX_CALLS if max_calls is None else max_calls

        # 这些状态不能放 self 以外的地方；用锁保护，因为子智能体可能并发调用
        self._lock = asyncio.Lock()
        self._calls = 0
        self._last_at = 0.0
        self._penalty = 0.0

    async def awrap_tool_call(self, request, handler):
        name = getattr(request, "name", "")
        if not self.pattern.search(name):
            return await handler(request)  # 不是 12306 的工具，直接放行

        async with self._lock:
            if self._calls >= self.max_calls:
                print(f"  [限流] {name} 被拦截：已达上限 {self.max_calls} 次", flush=True)
                return _tool_message(
                    request,
                    f"本次会话的 12306 查询已达上限（{self.max_calls} 次）。"
                    "请用已有的车次信息完成规划，不要再次查询；"
                    "如果确实需要更多班次，请让用户开启新会话。",
                )
            wait = max(0.0, self.min_interval + self._penalty - (time.monotonic() - self._last_at))
            self._calls += 1
            call_no = self._calls

        if wait > 0:
            print(f"  [限流] {name} 等待 {wait:.1f}s（第 {call_no} 次查询）", flush=True)
            await asyncio.sleep(wait)

        async with self._lock:
            self._last_at = time.monotonic()

        try:
            return await handler(request)
        except Exception:
            async with self._lock:
                self._penalty = min(30.0, (self._penalty or 1.0) * 2)  # 3 → 6 → 12 …
            raise

    @property
    def stats(self) -> str:
        return f"12306 查询 {self._calls}/{self.max_calls} 次"


def build_middlewares() -> list:
    """组装中间件。顺序就是执行顺序（外层在前）。"""
    return [TraceMiddleware(), RateLimitMiddleware()]
