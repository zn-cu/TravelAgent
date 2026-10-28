"""Basic 鉴权中间件的单元测试（纯 ASGI，不需要联网、不需要 fastapi）。"""

from __future__ import annotations

import asyncio
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.auth import BasicAuthMiddleware  # noqa: E402


def basic_header(user: str, pwd: str) -> tuple[bytes, bytes]:
    token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return (b"authorization", f"Basic {token}".encode())


class Recorder:
    """收集中间件发给客户端的所有 ASGI 消息，并记录下游是否被调用。"""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.called_downstream = False

    async def send(self, message: dict) -> None:
        self.messages.append(message)

    @property
    def status(self) -> int | None:
        return self.messages[0]["status"] if self.messages else None

    def header(self, name: bytes) -> bytes | None:
        for key, value in self.messages[0].get("headers", []):
            if key.lower() == name:
                return value
        return None


async def downstream(scope, receive, send):
    """冒充真实的 FastAPI 应用：收到请求就回 200。"""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def make_app(username="sunlianwu", password="secret"):
    recorder = Recorder()

    async def app(scope, receive, send):
        recorder.called_downstream = True
        await downstream(scope, receive, send)

    middleware = BasicAuthMiddleware(app, username=username, password=password)
    return middleware, recorder


def call(middleware, headers=None, scope_type="http", query_string=b"", path="/"):
    scope = {
        "type": scope_type,
        "headers": headers or [],
        "query_string": query_string,
        "path": path,
    }
    out = Recorder()

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        out.messages.append(message)

    asyncio.run(middleware(scope, receive, send))
    return out


class TestBasicAuth(unittest.TestCase):
    def test_no_password_means_open(self):
        """没配 ACCESS_PASSWORD 时不拦（本地开发用）。"""
        middleware, recorder = make_app(password="")
        out = call(middleware)
        self.assertEqual(out.status, 200)
        self.assertTrue(recorder.called_downstream)

    def test_missing_header_returns_401_with_challenge(self):
        middleware, recorder = make_app()
        out = call(middleware)
        self.assertEqual(out.status, 401)
        self.assertFalse(recorder.called_downstream)
        # 没有这个头，浏览器不会弹登录框
        self.assertIn(b"Basic", out.header(b"www-authenticate") or b"")

    def test_wrong_password_rejected(self):
        middleware, recorder = make_app()
        out = call(middleware, headers=[basic_header("sunlianwu", "wrong")])
        self.assertEqual(out.status, 401)
        self.assertFalse(recorder.called_downstream)

    def test_wrong_username_rejected(self):
        middleware, _ = make_app()
        out = call(middleware, headers=[basic_header("someone", "secret")])
        self.assertEqual(out.status, 401)

    def test_correct_credentials_pass(self):
        middleware, recorder = make_app()
        out = call(middleware, headers=[basic_header("sunlianwu", "secret")])
        self.assertEqual(out.status, 200)
        self.assertTrue(recorder.called_downstream)

    def test_header_name_is_case_insensitive(self):
        middleware, _ = make_app()
        token = base64.b64encode(b"sunlianwu:secret").decode()
        out = call(middleware, headers=[(b"Authorization", f"Basic {token}".encode())])
        self.assertEqual(out.status, 200)

    def test_non_basic_scheme_rejected(self):
        middleware, _ = make_app()
        out = call(middleware, headers=[(b"authorization", b"Bearer abcdef")])
        self.assertEqual(out.status, 401)

    def test_garbage_header_rejected(self):
        middleware, _ = make_app()
        out = call(middleware, headers=[(b"authorization", b"Basic ???not-base64")])
        self.assertEqual(out.status, 401)

    def test_lifespan_scope_passes_through(self):
        """启动时的 lifespan 事件不能被鉴权拦掉，否则服务起不来。"""
        middleware, recorder = make_app()
        call(middleware, scope_type="lifespan")
        self.assertTrue(recorder.called_downstream)

    # ------------------------------------------------------------------ #
    # 带签名的下载 URL（手机下载不依赖登录态）
    # ------------------------------------------------------------------ #
    def test_signed_download_url_without_auth(self):
        from urllib.parse import quote

        from travel_agent.downloads import sign

        middleware, recorder = make_app()
        middleware.signed_paths = ("/api/download",)
        token = sign("策划书.md", "secret")
        query = f"name={quote('策划书.md')}&t={quote(token)}".encode()

        out = call(middleware, headers=[], query_string=query, path="/api/download")
        self.assertEqual(out.status, 200)
        self.assertTrue(recorder.called_downstream)

    def test_signature_does_not_open_other_paths(self):
        from urllib.parse import quote

        from travel_agent.downloads import sign

        middleware, recorder = make_app()
        middleware.signed_paths = ("/api/download",)
        query = f"name=x&t={quote(sign('x', 'secret'))}".encode()

        out = call(middleware, headers=[], query_string=query, path="/api/sessions")
        self.assertEqual(out.status, 401)
        self.assertFalse(recorder.called_downstream)

    def test_signed_path_with_bad_token_still_401(self):
        middleware, recorder = make_app()
        middleware.signed_paths = ("/api/download",)
        out = call(middleware, headers=[], query_string=b"name=x&t=1.deadbeef", path="/api/download")
        self.assertEqual(out.status, 401)
        self.assertFalse(recorder.called_downstream)


if __name__ == "__main__":
    unittest.main()
