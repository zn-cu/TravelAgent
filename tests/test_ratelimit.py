"""限流中间件的单元测试。

这个测试需要安装 langchain；没装就自动跳过，不会让整个测试套挂掉。

    python -m unittest discover tests -v
"""

from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from travel_agent.middleware import RateLimitMiddleware
except ImportError as exc:  # pragma: no cover
    raise unittest.SkipTest(f"需要先安装依赖才能测中间件：{exc}")


class FakeRequest:
    """冒充 ToolCallRequest，中间件只用到 name / args / tool_call 三个属性。"""

    def __init__(self, name: str, args: dict | None = None):
        self.name = name
        self.args = args or {}
        self.tool_call = {"id": "call_1", "name": name, "args": self.args}


class TestRateLimit(unittest.TestCase):
    def test_non_rail_tool_passes_through(self):
        """不匹配的工具有多少放多少，不受限流影响。"""
        guard = RateLimitMiddleware(min_interval=0, max_calls=2)
        calls = []

        async def handler(_request):
            calls.append(1)
            return "ok"

        async def run():
            for _ in range(5):
                await guard.awrap_tool_call(FakeRequest("maps_text_search"), handler)

        asyncio.run(run())
        self.assertEqual(len(calls), 5)

    def test_min_interval_enforced(self):
        """两次 12306 查询之间至少要等 min_interval 秒。"""
        guard = RateLimitMiddleware(min_interval=0.3, max_calls=10)

        async def handler(_request):
            return "ok"

        async def run():
            start = time.perf_counter()
            for _ in range(2):
                await guard.awrap_tool_call(FakeRequest("get-tickets"), handler)
            return time.perf_counter() - start

        elapsed = asyncio.run(run())
        self.assertGreaterEqual(elapsed, 0.3)

    def test_max_calls_blocks_with_message(self):
        """超过上限不再真正调用工具，而是返回一句提示。"""
        guard = RateLimitMiddleware(min_interval=0, max_calls=2)
        calls = []

        async def handler(_request):
            calls.append(1)
            return "ok"

        async def run():
            results = []
            for _ in range(4):
                results.append(await guard.awrap_tool_call(FakeRequest("get-tickets"), handler))
            return results

        results = asyncio.run(run())
        self.assertEqual(len(calls), 2)  # 只真正执行了 2 次
        self.assertIn("上限", str(getattr(results[-1], "content", results[-1])))


if __name__ == "__main__":
    unittest.main()
