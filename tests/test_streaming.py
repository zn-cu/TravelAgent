"""阶段日志事件翻译层的单元测试。

刻意不依赖 langchain：streaming.py 用「鸭子类型」判断消息对象，
所以几个假对象就能把事件协议测干净。

    python -m unittest discover tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.streaming import (  # noqa: E402
    agent_label,
    dumps,
    final_proposal,
    final_state_message,
    interrupt_items,
    short_tool_name,
    steps_from_update,
    stream_events,
    text_of,
)


# --------------------------------------------------------------------------- #
# 假对象
# --------------------------------------------------------------------------- #
class FakeAI:
    type = "ai"

    def __init__(self, tool_calls=None, content="") -> None:
        self.tool_calls = tool_calls or []
        self.content = content


class FakeToolMessage:
    type = "tool"

    def __init__(self, tool_call_id: str, name: str, content: str = "ok", status="success") -> None:
        self.tool_call_id = tool_call_id
        self.name = name
        self.content = content
        self.status = status


class FakeInterrupt:
    def __init__(self, interrupt_id, value) -> None:
        self.id = interrupt_id
        self.value = value


class AIMessageChunk:  # 用这个名字：stream_events 靠类名识别模型增量
    def __init__(self, text: str) -> None:
        self.content = text


class FakeAgent:
    def __init__(self, items) -> None:
        self._items = items

    async def astream(self, payload, config, stream_mode, subgraphs):  # noqa: ARG002
        for item in self._items:
            yield item


class FakeState:
    def __init__(self, values) -> None:
        self.values = values


class FakeAgentWithState:
    def __init__(self, values=None) -> None:
        self._values = values or {"messages": [FakeAI(content="最终方案如下"), FakeAI(content="")]}

    async def aget_state(self, cfg):  # noqa: ARG002
        return FakeState(self._values)


def tool_call(call_id="c1", name="amap_maps_text_search", **args):
    return {"id": call_id, "name": name, "args": args or {"keywords": "成都 景点"}}


# --------------------------------------------------------------------------- #
class TestHelpers(unittest.TestCase):
    def test_short_tool_name(self):
        self.assertEqual(short_tool_name("amap_maps_text_search"), "maps_text_search")
        self.assertEqual(short_tool_name("rail12306_get-tickets"), "get-tickets")
        self.assertEqual(short_tool_name("calculate_budget"), "calculate_budget")

    def test_text_of(self):
        self.assertEqual(text_of(FakeAI(content="你好")), "你好")
        self.assertEqual(
            text_of(FakeAI(content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}])),
            "ab",
        )
        self.assertEqual(text_of(object()), "")

    def test_agent_label(self):
        self.assertEqual(agent_label((), None), "项目经理")
        self.assertEqual(agent_label((), {"lc_agent_name": "budget-keeper"}), "budget-keeper")
        self.assertEqual(agent_label(("tools:abc",), None), "子智能体")


class TestStepsFromUpdate(unittest.TestCase):
    def test_tool_open(self):
        data = {"model": {"messages": [FakeAI(tool_calls=[tool_call()])]}}
        actions = steps_from_update(data, "景点推荐官")
        self.assertEqual(len(actions), 1)
        action = actions[0]
        self.assertEqual(action["_action"], "open")
        self.assertEqual(action["kind"], "tool")
        self.assertEqual(action["agent"], "景点推荐官")
        self.assertEqual(action["label"], "搜索景点")  # 走了人话映射
        self.assertEqual(action["key"], "c1")

    def test_task_becomes_handoff(self):
        call = tool_call(name="task", subagent_type="transport-planner", description="查交通")
        actions = steps_from_update({"model": {"messages": [FakeAI(tool_calls=[call])]}}, "项目经理")
        self.assertEqual(actions[0]["kind"], "handoff")
        self.assertIn("transport-planner", actions[0]["label"])

    def test_unknown_tool_falls_back_to_name(self):
        call = tool_call(name="whatever_tool")
        actions = steps_from_update({"model": {"messages": [FakeAI(tool_calls=[call])]}}, "项目经理")
        self.assertEqual(actions[0]["label"], "调用 whatever_tool")

    def test_tool_close(self):
        data = {"tools": {"messages": [FakeToolMessage("c1", "amap_maps_geo")]}}
        actions = steps_from_update(data, "出行规划师")
        self.assertEqual(actions[0]["_action"], "close")
        self.assertTrue(actions[0]["ok"])

    def test_error_result(self):
        msg = FakeToolMessage("c1", "calculate_budget", status="error")
        actions = steps_from_update({"tools": {"messages": [msg]}}, "财务管家")
        self.assertFalse(actions[0]["ok"])

    def test_ignores_noise(self):
        self.assertEqual(steps_from_update({"model": {}}, "x"), [])
        self.assertEqual(steps_from_update("不是字典", "x"), [])
        self.assertEqual(steps_from_update({"__interrupt__": []}, "x"), [])


class TestInterrupt(unittest.TestCase):
    def test_items(self):
        value = {
            "action_requests": [{"name": "confirm_itinerary", "args": {"summary": "成都 3 天"}}],
            "review_configs": [
                {"action_name": "confirm_itinerary", "allowed_decisions": ["approve", "edit"]}
            ],
        }
        items = interrupt_items({"__interrupt__": [FakeInterrupt(7, value)]})
        self.assertEqual(items[0]["interrupt_id"], 7)
        action = items[0]["actions"][0]
        self.assertEqual(action["name"], "confirm_itinerary")
        self.assertEqual(action["allowed"], ["approve", "edit"])


# --------------------------------------------------------------------------- #
class TestStreamEvents(unittest.IsolatedAsyncioTestCase):
    async def test_tool_open_then_close(self):
        items = [
            ((), "updates", {"model": {"messages": [FakeAI(tool_calls=[tool_call()])]}}),
            ((), "updates", {"tools": {"messages": [FakeToolMessage("c1", "amap_maps_text_search")]}}),
        ]
        events = [e async for e in stream_events(FakeAgent(items), {}, {})]

        opened = events[0]
        self.assertEqual(opened["type"], "step")
        self.assertEqual(opened["status"], "running")
        self.assertEqual(opened["kind"], "tool")

        closed = next(e for e in events if e["type"] == "step" and e["status"] == "ok")
        self.assertEqual(closed["id"], opened["id"])
        self.assertIsNotNone(closed["ms"])

    async def test_agent_thinking_step(self):
        """一段正文增量 → 一条「正在思考」，而且只在开始时发一次。"""
        items = [
            ((), "messages", (AIMessageChunk("我"), {"lc_agent_name": "项目经理"})),
            ((), "messages", (AIMessageChunk("先看"), {"lc_agent_name": "项目经理"})),
            ((), "updates", {"model": {"messages": [FakeAI(tool_calls=[tool_call()])]}}),
        ]
        events = [e async for e in stream_events(FakeAgent(items), {}, {})]
        agent_steps = [e for e in events if e.get("kind") == "agent"]
        self.assertEqual(len(agent_steps), 1)  # 两条增量只产生一条
        self.assertEqual(agent_steps[0]["agent"], "项目经理")

    async def test_running_steps_are_closed_at_end(self):
        items = [((), "updates", {"model": {"messages": [FakeAI(tool_calls=[tool_call()])]}})]
        events = [e async for e in stream_events(FakeAgent(items), {}, {})]
        self.assertEqual(events[-1]["status"], "ok")  # 收尾把 running 关掉

    async def test_subagent_name_is_remembered(self):
        """updates 流里没有 lc_agent_name，要从 messages 流里记住再反查。

        不这么做的话，界面上所有专家都显示成「子智能体」，看不出是谁在干活。
        """
        ns = ("tools:abc",)
        items = [
            (ns, "messages", (AIMessageChunk("我查一下"), {"lc_agent_name": "budget-keeper"})),
            (ns, "updates", {"model": {"messages": [FakeAI(tool_calls=[tool_call()])]}}),
        ]
        events = [e async for e in stream_events(FakeAgent(items), {}, {})]
        agents = {e.get("agent") for e in events if e["type"] == "step"}
        self.assertIn("budget-keeper", agents)
        self.assertNotIn("子智能体", agents)

    async def test_interrupt_event(self):
        value = {
            "action_requests": [{"name": "confirm_itinerary", "args": {}}],
            "review_configs": [{"action_name": "confirm_itinerary", "allowed_decisions": ["approve"]}],
        }
        items = [((), "updates", {"__interrupt__": [FakeInterrupt(1, value)]})]
        events = [e async for e in stream_events(FakeAgent(items), {}, {})]
        self.assertEqual(events[0]["type"], "interrupt")
        self.assertEqual(events[0]["items"][0]["interrupt_id"], 1)

    async def test_exception_becomes_error(self):
        class Broken:
            async def astream(self, *args, **kwargs):
                raise RuntimeError("模型 401")
                yield  # pragma: no cover

        events = [e async for e in stream_events(Broken(), {}, {})]
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("模型 401", events[-1]["message"])

    async def test_final_state_message(self):
        self.assertEqual(await final_state_message(FakeAgentWithState(), {}), "最终方案如下")

        class Broken:
            async def aget_state(self, cfg):
                raise RuntimeError("没有 checkpointer")

        self.assertEqual(await final_state_message(Broken(), {}), "")


class TestFinalProposal(unittest.IsolatedAsyncioTestCase):
    """只有 publish_proposal 成功落盘，才算"本轮产出了策划书"。"""

    async def test_finds_published_file(self):
        message = FakeToolMessage("c9", "publish_proposal", '{"ok": true, "file": "策划书-1028.md"}')
        agent = FakeAgentWithState({"messages": [FakeAI(content="已保存"), message]})
        self.assertEqual(await final_proposal(agent, {}), "策划书-1028.md")

    async def test_returns_none_when_never_published(self):
        agent = FakeAgentWithState({"messages": [FakeAI(content="请告诉我出发地")]})
        self.assertIsNone(await final_proposal(agent, {}))

    async def test_returns_none_when_publish_failed(self):
        message = FakeToolMessage("c9", "publish_proposal", '{"ok": false, "error": "太短"}')
        agent = FakeAgentWithState({"messages": [message]})
        self.assertIsNone(await final_proposal(agent, {}))

    async def test_survives_broken_json(self):
        message = FakeToolMessage("c9", "publish_proposal", "不是 json")
        agent = FakeAgentWithState({"messages": [message]})
        self.assertIsNone(await final_proposal(agent, {}))

    async def test_survives_missing_checkpointer(self):
        class Broken:
            async def aget_state(self, cfg):
                raise RuntimeError("没有 checkpointer")

        self.assertIsNone(await final_proposal(Broken(), {}))


class TestDumps(unittest.TestCase):
    def test_one_line(self):
        line = dumps({"type": "step", "label": "第一行\n第二行"})
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(line.count("\n"), 1)
        self.assertEqual(json.loads(line)["label"], "第一行\n第二行")


if __name__ == "__main__":
    unittest.main()
