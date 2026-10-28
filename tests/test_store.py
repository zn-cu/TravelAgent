"""会话存储的单元测试（只用内存版，不需要 Redis）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.store import MAX_LOG, MemoryStore, new_session  # noqa: E402


class TestMemoryStore(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = MemoryStore()

    async def test_create_get(self):
        session = new_session()
        await self.store.save(session)

        loaded = await self.store.get(session["id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "idle")
        self.assertEqual(loaded["title"], "新会话")

    async def test_get_returns_copy(self):
        """取出来的是副本，改它不该影响存储里的数据。"""
        session = new_session()
        await self.store.save(session)

        loaded = await self.store.get(session["id"])
        loaded["title"] = "被改坏的标题"

        again = await self.store.get(session["id"])
        self.assertEqual(again["title"], "新会话")

    async def test_save_updates_timestamp_and_log(self):
        session = new_session()
        await self.store.save(session)
        first = (await self.store.get(session["id"]))["updated_at"]

        session["status"] = "done"
        session["log"] = [{"id": "s1", "kind": "tool", "label": "搜索景点"}]
        session["result"] = {"message": "# 方案", "file": None}
        await self.store.save(session)

        loaded = await self.store.get(session["id"])
        self.assertEqual(loaded["status"], "done")
        self.assertEqual(len(loaded["log"]), 1)
        self.assertEqual(loaded["result"]["message"], "# 方案")
        self.assertGreaterEqual(loaded["updated_at"], first)

    async def test_log_is_trimmed(self):
        """日志不能无限涨，超过上限只留最后 MAX_LOG 条。"""
        session = new_session()
        session["log"] = [{"id": f"s{i}"} for i in range(MAX_LOG + 50)]
        await self.store.save(session)

        loaded = await self.store.get(session["id"])
        self.assertEqual(len(loaded["log"]), MAX_LOG)
        self.assertEqual(loaded["log"][-1]["id"], f"s{MAX_LOG + 49}")

    async def test_list_is_sorted_by_time(self):
        """最近更新的排前面。存储层自己保证时间戳严格递增，不用外部 sleep。"""
        older = new_session("旧会话")
        await self.store.save(older)
        newer = new_session("新会话")
        await self.store.save(newer)

        summaries = await self.store.list_summaries()
        self.assertEqual(summaries[0]["id"], newer["id"])
        self.assertEqual(summaries[0]["title"], "新会话")
        self.assertEqual(summaries[1]["id"], older["id"])

    async def test_delete(self):
        session = new_session()
        await self.store.save(session)
        self.assertTrue(await self.store.delete(session["id"]))
        self.assertIsNone(await self.store.get(session["id"]))
        self.assertFalse(await self.store.delete(session["id"]))

    async def test_missing_returns_none(self):
        self.assertIsNone(await self.store.get("不存在的id"))


if __name__ == "__main__":
    unittest.main()
