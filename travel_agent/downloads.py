"""给下载链接签名，让它不依赖 Basic Auth 登录态。

为什么需要它
------------
手机浏览器（QQ / 夸克 / 微信）点"下载"时，往往不是复用当前页面的连接，而是
**另起一个下载任务**。这个新连接通常不带页面的 Authorization 头，
于是服务端回 401，浏览器上就显示「下载错误」。

解决办法：在下载 URL 里带一个 HMAC 签名，服务端校验签名即可放行——
既不用登录态，也不会把文件变成公开链接（链接有有效期，且绑定文件名）。

    /api/download?name=策划书-1028.md&t=1761000000.<32位hmac>

签名密钥用 ACCESS_PASSWORD（改了密码，旧链接自动失效）。
纯标准库实现，可离线单测。
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import parse_qs, quote

#: 下载链接有效期（秒）。给 6 小时：够用户慢慢点开、转发给自己，
#: 又不会长到让别人抓到一个链接就能永久下载。
DEFAULT_TTL = 6 * 3600


def _signature(name: str, exp: int, secret: str) -> str:
    payload = f"{name}:{exp}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:32]


def sign(name: str, secret: str, ttl: int = DEFAULT_TTL, now: float | None = None) -> str:
    """生成 `过期时间戳.签名`。"""
    exp = int((now if now is not None else time.time()) + ttl)
    return f"{exp}.{_signature(name, exp, secret)}"


def verify(name: str, token: str, secret: str, now: float | None = None) -> bool:
    """校验令牌：签名对不对 + 有没有过期。"""
    if not token or not secret:
        return False
    exp_part, _, sig = token.partition(".")
    if not exp_part or not sig:
        return False
    try:
        exp = int(exp_part)
    except ValueError:
        return False
    if exp < (now if now is not None else time.time()):
        return False
    return hmac.compare_digest(sig, _signature(name, exp, secret))


def url_for(path: str, name: str, secret: str, ttl: int = DEFAULT_TTL) -> str:
    """拼出带签名的下载 URL。"""
    return f"{path}?name={quote(name)}&t={quote(sign(name, secret, ttl))}"


def signed_request_ok(path: str, path_allowlist, query_string: bytes | str, secret: str,
                      now: float | None = None) -> bool:
    """ASGI 用：判断这个请求是不是"带合法签名的下载请求"。

    path_allowlist 只放行下载类路径，避免签名机制被用到别的接口上。
    """
    if path not in tuple(path_allowlist):
        return False
    if isinstance(query_string, bytes):
        query_string = query_string.decode("latin-1")
    params = parse_qs(query_string or "")
    names = params.get("name") or []
    tokens = params.get("t") or []
    if not names or not tokens:
        return False
    return verify(names[0], tokens[0], secret, now=now)
