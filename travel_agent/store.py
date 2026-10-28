"""会话存储：内存版（默认）/ Redis 版（可选）。

为什么要抽象这一层：
    前端要"新开会话 + 切换会话"，就必须有人在服务端记住每个会话的日志和结果。
    先做内存版能跑；等你需要"重启后会话还在"或者跑多个 worker，再配 REDIS_URL 切到 Redis
    —— 业务代码一行都不用改。

会话文档（两种后端存的是同一个结构）：
    {
      "id": "a1b2c3",
      "title": "我想去成都玩3天",
      "created_at": "...", "updated_at": "...",
      "status": "idle | running | waiting | done | error",
      "log": [{"id","kind","agent","label","status","ms"}, ...],
      "result": {"message": "...", "file": "xxx.md"} | None,
      "pending": [{"interrupt_id","actions":[...]}] | None   # 刷新页面后还能继续确认
    }
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #
DEFAULT_TITLE = "新会话"

#: 一个会话最多留多少条日志，防止内存/Redis 无限涨
MAX_LOG = 200


def new_session(title: str = DEFAULT_TITLE) -> dict[str, Any]:
    now = time.time()
    return {
        "id": uuid.uuid4().hex[:8],
        "title": title,
        "created_at": now,
        "updated_at": now,
        "status": "idle",
        "log": [],
        "result": None,
        "pending": None,
    }


class SessionStore:
    """所有后端要实现的接口。"""

    async def save(self, session: dict[str, Any]) -> None:
        raise NotImplementedError

    async def get(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    async def list_summaries(self, limit: int = 30) -> list[dict[str, Any]]:
        raise NotImplementedError

    async def delete(self, session_id: str) -> bool:
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# 内存版
# --------------------------------------------------------------------------- #
class MemoryStore(SessionStore):
    """单进程内存。重启就没了，但够学习用，零依赖。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        # 保存时间戳用「严格递增」的时钟：Windows 上 time.time() 精度可能只有 15ms，
        # 连续保存两个会话会拿到同一个时间，排序结果不稳定。加一个自增下限就没这问题。
        self._clock = 0.0

    async def save(self, session: dict[str, Any]) -> None:
        async with self._lock:
            self._clock = max(time.time(), self._clock + 1e-6)
            session["updated_at"] = self._clock
            if len(session.get("log", [])) > MAX_LOG:
                session["log"] = session["log"][-MAX_LOG:]
            self._data[session["id"]] = session

    async def get(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            found = self._data.get(session_id)
            return json.loads(json.dumps(found, ensure_ascii=False)) if found else None

    async def list_summaries(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self._lock:
            items = sorted(self._data.values(), key=lambda s: s["updated_at"], reverse=True)
            return [
                {
                    "id": s["id"],
                    "title": s["title"],
                    "status": s["status"],
                    "updated_at": s["updated_at"],
                }
                for s in items[:limit]
            ]

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            return self._data.pop(session_id, None) is not None


# --------------------------------------------------------------------------- #
# Redis 版（可选）
# --------------------------------------------------------------------------- #
class RedisStore(SessionStore):
    """会话写到 Redis。同一个 Redis 可以给多个进程/多个 worker 共用。

    装：pip install "redis>=5"
    配：.env 里写 REDIS_URL=redis://:密码@127.0.0.1:6379/0
    """

    KEY = "travel:sessions"

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None
        self._lock = asyncio.Lock()

    async def _redis(self):
        async with self._lock:
            if self._client is None:
                try:
                    from redis.asyncio import from_url
                except ImportError as exc:  # pragma: no cover
                    raise SystemExit(
                        "配了 REDIS_URL 但没装 redis 客户端：pip install \"redis>=5\""
                    ) from exc
                self._client = from_url(self._url, decode_responses=True)
            return self._client

    async def save(self, session: dict[str, Any]) -> None:
        client = await self._redis()
        session["updated_at"] = time.time()
        if len(session.get("log", [])) > MAX_LOG:
            session["log"] = session["log"][-MAX_LOG:]
        await client.hset(self.KEY, session["id"], json.dumps(session, ensure_ascii=False))

    async def get(self, session_id: str) -> dict[str, Any] | None:
        client = await self._redis()
        raw = await client.hget(self.KEY, session_id)
        return json.loads(raw) if raw else None

    async def list_summaries(self, limit: int = 30) -> list[dict[str, Any]]:
        client = await self._redis()
        raw_map = await client.hgetall(self.KEY)
        sessions = [json.loads(v) for v in raw_map.values()]
        sessions.sort(key=lambda s: s.get("updated_at", 0), reverse=True)
        return [
            {
                "id": s["id"],
                "title": s.get("title", DEFAULT_TITLE),
                "status": s.get("status", "idle"),
                "updated_at": s.get("updated_at", 0),
            }
            for s in sessions[:limit]
        ]

    async def delete(self, session_id: str) -> bool:
        client = await self._redis()
        return bool(await client.hdel(self.KEY, session_id))


# --------------------------------------------------------------------------- #
def make_store() -> SessionStore:
    """有 REDIS_URL 就用 Redis，否则用内存。启动时打印一行，方便确认走的是哪条。"""
    url = os.getenv("REDIS_URL", "").strip()
    if url:
        print(f"会话存储：Redis（{url.split('@')[-1]}）", flush=True)
        return RedisStore(url)
    print("会话存储：内存（重启后会话会丢失；要持久化就配 REDIS_URL）", flush=True)
    return MemoryStore()
