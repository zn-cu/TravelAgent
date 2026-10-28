"""下载链接签名的单元测试（纯标准库）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.downloads import (  # noqa: E402
    DEFAULT_TTL,
    sign,
    signed_request_ok,
    url_for,
    verify,
)

SECRET = "sunlianwu"
NAME = "策划书-1028-134935.md"


class TestSignVerify(unittest.TestCase):
    def test_roundtrip(self):
        token = sign(NAME, SECRET, now=1_000_000)
        self.assertTrue(verify(NAME, token, SECRET, now=1_000_100))

    def test_expired(self):
        token = sign(NAME, SECRET, ttl=60, now=1_000_000)
        self.assertTrue(verify(NAME, token, SECRET, now=1_000_059))
        self.assertFalse(verify(NAME, token, SECRET, now=1_000_061))

    def test_token_is_bound_to_filename(self):
        token = sign(NAME, SECRET, now=1_000_000)
        self.assertFalse(verify("别的文件.md", token, SECRET, now=1_000_100))

    def test_wrong_secret_rejected(self):
        token = sign(NAME, SECRET, now=1_000_000)
        self.assertFalse(verify(NAME, token, "换了个密码", now=1_000_100))

    def test_tampered_signature_rejected(self):
        token = sign(NAME, SECRET, now=1_000_000)
        exp, _, sig = token.partition(".")
        broken = f"{exp}.{'0' * len(sig)}"
        self.assertFalse(verify(NAME, broken, SECRET, now=1_000_100))

    def test_garbage_tokens_rejected(self):
        for bad in ("", "abc", "12345", "12345.", ".abcdef", "abc.def"):
            self.assertFalse(verify(NAME, bad, SECRET, now=1_000_100), bad)

    def test_empty_secret_is_never_valid(self):
        self.assertFalse(verify(NAME, sign(NAME, "x", now=1_000_000), "", now=1_000_100))

    def test_default_ttl_is_reasonable(self):
        self.assertGreaterEqual(DEFAULT_TTL, 3600)
        self.assertLessEqual(DEFAULT_TTL, 24 * 3600)


class TestUrlFor(unittest.TestCase):
    def test_contains_encoded_name_and_token(self):
        url = url_for("/api/download", NAME, SECRET)
        self.assertTrue(url.startswith("/api/download?name="))
        self.assertIn("&t=", url)
        self.assertNotIn("策划书", url)  # 中文必须编码

    def test_token_from_url_verifies(self):
        url = url_for("/api/proposal.pdf", NAME, SECRET)
        token = url.split("&t=", 1)[1]
        self.assertTrue(verify(NAME, token, SECRET))


class TestSignedRequest(unittest.TestCase):
    ALLOW = ("/api/download", "/api/proposal.pdf")

    def _query(self, token):
        from urllib.parse import quote

        return f"name={quote(NAME)}&t={quote(token)}"

    def test_valid_request_allowed(self):
        token = sign(NAME, SECRET, now=1_000_000)
        self.assertTrue(
            signed_request_ok("/api/download", self.ALLOW, self._query(token), SECRET, now=1_000_100)
        )

    def test_path_not_in_allowlist_rejected(self):
        """签名机制不能被用在别的接口上（比如 /api/sessions）。"""
        token = sign(NAME, SECRET, now=1_000_000)
        self.assertFalse(
            signed_request_ok("/api/sessions", self.ALLOW, self._query(token), SECRET, now=1_000_100)
        )

    def test_missing_token_rejected(self):
        self.assertFalse(signed_request_ok("/api/download", self.ALLOW, "", SECRET))

    def test_bytes_query_string_supported(self):
        token = sign(NAME, SECRET, now=1_000_000)
        query = self._query(token).encode("latin-1")
        self.assertTrue(
            signed_request_ok("/api/download", self.ALLOW, query, SECRET, now=1_000_100)
        )


if __name__ == "__main__":
    unittest.main()
