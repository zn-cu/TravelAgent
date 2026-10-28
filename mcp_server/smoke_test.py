"""MCP 自检：不启动 Agent、不花 token，先确认「MCP 这一层」是通的。

    python mcp_server/smoke_test.py

它扮演 MCP 客户端，拉起 server.py 子进程，走完整流程：
    initialize → tools/list → tools/call

这条命令过不了，就别去怀疑 Agent，问题一定在 MCP 或环境。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "server.py"


def force_utf8() -> None:
    """Windows 控制台默认 GBK，中文输出会炸；统一改 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


class McpStdioClient:
    """极简 MCP 客户端：一行一条 JSON-RPC 2.0 消息。"""

    def __init__(self) -> None:
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # 先缓存，失败时再打出来
            text=True,
            encoding="utf-8",
            env=env,
        )
        self._id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps(
                {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}},
                ensure_ascii=False,
            )
            + "\n"
        )
        self.proc.stdin.flush()
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server 已退出，没有返回数据（看下面的 stderr）")
        return json.loads(line)

    def notify(self, method: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": {}}) + "\n"
        )
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        finally:
            self.proc.wait(timeout=10)
        if self.proc.stderr is not None:
            log = self.proc.stderr.read().strip()
            self.proc.stderr.close()
            if log and self.proc.returncode:
                print("--- server stderr ---\n" + log)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def main() -> int:
    force_utf8()

    if not (_has("fastmcp") or _has("mcp")):
        print('缺少 FastMCP，先装依赖再自检：pip install "fastmcp>=2.0"')
        return 1

    client = McpStdioClient()
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "smoke-test", "version": "1.0.0"},
            },
        )
        server_info = init.get("result", {}).get("serverInfo", {})
        print(f"[1/3] initialize ok -> {server_info.get('name')} v{server_info.get('version')}")
        client.notify("notifications/initialized")

        tools = client.request("tools/list").get("result", {}).get("tools", [])
        print(f"[2/3] tools/list ok -> {[t['name'] for t in tools]}")

        result = client.request(
            "tools/call",
            {"name": "calculate_budget", "arguments": {"items": "高铁:1460,门票:640,酒店:600*2"}},
        ).get("result", {})
        payload = json.loads(result.get("content", [{}])[0].get("text", "{}"))
        print(f"[3/3] tools/call ok -> total={payload.get('total')} grand_total={payload.get('grand_total')}")

        if not payload.get("ok"):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        print("\nMCP 自检通过")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
