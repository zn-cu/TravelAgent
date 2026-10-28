"""HTTP Basic 鉴权中间件（纯 ASGI 实现，零第三方依赖）。

为什么写在应用里，而不是交给 Nginx/Caddy：
    这台服务器的 80/443 已经被别的项目占用了，再装一个反代容易打架。
    应用自己带一层 Basic Auth，就不用动别人，也不用额外进程。

行为：
    - 配了 ACCESS_PASSWORD → 所有请求要过账号密码（浏览器会弹原生登录框）
    - 没配 ACCESS_PASSWORD → 直接放行（本地开发方便，但公网暴露要自己负责）

刻意只用 ASGI 三件套（scope / receive / send）手写 401 响应，
不 import starlette —— 这样这个文件能离线单测，也不给依赖添乱。
"""

from __future__ import annotations

import base64
import secrets

from . import downloads


class BasicAuthMiddleware:
    """给 ASGI 应用套一层 Basic Auth。

    用法（FastAPI）：
        app.add_middleware(BasicAuthMiddleware, username="sunlianwu", password="xxx")

    `signed_paths` 里的路径允许用「带签名的 URL」免登录访问——手机浏览器下载文件时
    会另起连接、丢掉 Authorization 头，靠签名才不会 401。密钥就是 password。
    """

    def __init__(
        self,
        app,
        username: str = "",
        password: str = "",
        realm: str = "travel-agent",
        signed_paths: tuple[str, ...] = (),
    ):
        self.app = app
        self.username = username or "admin"
        self.password = password or ""
        self.realm = realm
        self.signed_paths = tuple(signed_paths)
        # 预编码，省得每次请求都拼
        self._expected = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()

    # ------------------------------------------------------------------ #
    async def __call__(self, scope, receive, send):
        # 没配密码就放行；非 HTTP 请求（lifespan / websocket 握手）也放行
        if not self.password or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        if self._authorized(scope.get("headers") or []):
            await self.app(scope, receive, send)
            return

        if downloads.signed_request_ok(
            scope.get("path", ""), self.signed_paths, scope.get("query_string", b""), self.password
        ):
            await self.app(scope, receive, send)
            return

        await self._deny(send)

    # ------------------------------------------------------------------ #
    def _authorized(self, headers) -> bool:
        for key, value in headers:
            if key.lower() != b"authorization":
                continue
            try:
                scheme, _, token = value.decode("latin-1").partition(" ")
            except (UnicodeDecodeError, AttributeError):
                return False
            if scheme.lower() != "basic":
                return False
            # 定长比较，避免用响应时间猜密码
            return secrets.compare_digest(token.strip(), self._expected)
        return False

    @staticmethod
    async def _deny(send) -> None:
        body = "401 Unauthorized：需要账号密码\n".encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    # 有这一行浏览器才会弹登录框
                    (b"www-authenticate", b'Basic realm="travel-agent", charset="UTF-8"'),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
