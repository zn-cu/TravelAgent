"""MCP 网关：逐个连上 MCP server，把工具整理成"能力"，再分发给三个专家。

这一层存在的意义：
    三个专家只需要说「我要 poi 和 weather」，
    不需要知道底层是哪个 server、工具全名叫什么。

三个关键设计（都是踩过坑才这么写的）：
1. **逐个连、各自超时**：一个 server 挂掉不能拖垮整个会话。三个 server 共用一个
   MCPAdapter 时，只要其中一个连不上，整体就卡死。
2. **远程 server 先做 TCP 探活**：高德是 HTTPS 远端，没有网或没配代理时连接会挂很久，
   先用 5 秒的 TCP 探测把它快速判掉。
3. **所有进度都 flush**：不然输出被 stdout 缓冲，用户只会看到"卡住"。
"""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlparse

from . import config


class ToolBox:
    """装着本次会话可用的全部 MCP 工具，以及能力映射结果。"""

    def __init__(self, tools: list, failed: dict[str, str] | None = None) -> None:
        self.all_tools = list(tools)
        self.by_name = {tool.name: tool for tool in self.all_tools}
        self.capabilities: dict[str, list[str]] = {}
        self.failed = failed or {}  # server 名 → 失败原因

    # ------------------------------------------------------------------ #
    # 匹配
    # ------------------------------------------------------------------ #
    def resolve(self) -> list[str]:
        """把候选名匹配成真实工具名；返回没匹配到的能力名单。"""
        missing: list[str] = []
        for capability, candidates in config.CAPABILITIES.items():
            matched: list[str] = []
            for candidate in candidates:
                real_name = self._match(candidate)
                # 去重很重要：候选名 "get-tickets" 和 "get_tickets" 可能落到同一个真实工具上
                if real_name and real_name not in matched:
                    matched.append(real_name)
            if not matched:
                missing.append(capability)
            self.capabilities[capability] = matched
        return missing

    def _match(self, candidate: str) -> str | None:
        """精确匹配优先，其次允许 server 给工具加前缀。"""
        if candidate in self.by_name:
            return candidate
        for name in self.by_name:
            if name.endswith(candidate) or name.endswith(candidate.replace("_", "-")):
                return name
        return None

    # ------------------------------------------------------------------ #
    # 取用
    # ------------------------------------------------------------------ #
    def tools(self, *capabilities: str) -> list:
        """按能力取工具对象（自动去重）。"""
        names: list[str] = []
        for capability in capabilities:
            names.extend(self.capabilities.get(capability, []))

        picked, seen = [], set()
        for name in names:
            if name not in seen:
                seen.add(name)
                picked.append(self.by_name[name])
        return picked

    def report(self) -> str:
        """打印用：能力 → 实际工具名。"""
        lines = ["能力映射："]
        for capability, names in self.capabilities.items():
            lines.append(f"  {capability:<12} -> {', '.join(names) if names else '（未匹配到）'}")
        if self.failed:
            lines.append("连不上的 server：")
            for name, reason in self.failed.items():
                lines.append(f"  {name:<12} {reason}")
        return "\n".join(lines)


async def _remote_probe(url: str, timeout: float = 6.0) -> str | None:
    """对远程 MCP server 做一次「TCP + TLS」探活。

    返回 None 表示能连；返回字符串表示连不上的原因。

    为什么不能只探 TCP：有些网络环境下 TCP 能通（甚至三次握手成功），
    但 TLS 会被网关直接重置，表现为"连接上去了但永远没有响应"。
    把 TLS 握手也探一遍，才能在几秒内给出明确原因，而不是让用户干等。
    """
    import ssl

    parsed = urlparse(url)
    host = parsed.hostname
    is_https = parsed.scheme != "http"
    port = parsed.port or (443 if is_https else 80)
    if not host:
        return "URL 里解析不出主机名"

    writer = None
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        if is_https:
            context = ssl.create_default_context()
            await asyncio.wait_for(
                writer.start_tls(context, server_hostname=host), timeout
            )
        return None
    except asyncio.TimeoutError:
        return f"连接 {host}:{port} 超时（>{timeout}s）"
    except ssl.SSLError as exc:
        return f"TLS 握手失败 {host}:{port}（{type(exc).__name__}；可能需要配代理）"
    except OSError as exc:
        return f"连不上 {host}:{port}（{type(exc).__name__}）"
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, AttributeError, ssl.SSLError):
                pass


@asynccontextmanager
async def open_toolbox(
    verbose: bool = True,
    timeout: float | None = None,
    skip: list[str] | None = None,
) -> AsyncIterator[ToolBox]:
    """逐个连上 MCP server，yield 一个 ToolBox；退出时自动清理子进程。"""
    try:
        from langchain.mcp import MCPAdapter
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            '缺少 MCP 支持，请先执行：pip install "langchain[mcp]>=1.4.0"\n' f"原始错误：{exc}"
        ) from exc

    connect_timeout = timeout or config.MCP_TIMEOUT
    servers = config.mcp_servers()

    for name in (config.MCP_SKIP if skip is None else skip):
        if name in servers:
            servers.pop(name)
            if verbose:
                print(f"  - 按配置跳过 {name}", flush=True)

    all_tools: list = []
    failed: dict[str, str] = {}

    async with AsyncExitStack() as stack:
        for name, server_conf in servers.items():
            if verbose:
                print(f"  → {name} ... ", end="", flush=True)
            started = time.perf_counter()

            # 远程 HTTP server 正常应该秒连；给它更短的超时，别让用户等 90 秒
            per_timeout = min(connect_timeout, 20) if "url" in server_conf else connect_timeout

            # 远程 server 先探活，避免在连接上硬等
            if "url" in server_conf:
                reason = await _remote_probe(server_conf["url"])
                if reason:
                    failed[name] = reason
                    if verbose:
                        print(f"跳过（{reason}）", flush=True)
                    continue

            try:
                adapter = await asyncio.wait_for(
                    stack.enter_async_context(MCPAdapter({"mcpServers": {name: server_conf}})),
                    per_timeout,
                )
                tools = await asyncio.wait_for(adapter.list_tools(), per_timeout)
            except asyncio.TimeoutError:
                failed[name] = f"超时（>{per_timeout:.0f}s）"
                if verbose:
                    print(f"超时（>{per_timeout:.0f}s）", flush=True)
                continue
            except Exception as exc:  # noqa: BLE001 - 一个 server 失败不能拖垮整体
                failed[name] = f"{type(exc).__name__}: {exc}"
                if verbose:
                    print(f"失败（{failed[name]}）", flush=True)
                continue

            all_tools.extend(tools)
            if verbose:
                print(f"ok，{len(tools)} 个工具（{time.perf_counter() - started:.1f}s）", flush=True)

        box = ToolBox(all_tools, failed)
        missing = box.resolve()

        if verbose:
            print(f"共发现 {len(all_tools)} 个工具", flush=True)
            print(box.report(), flush=True)
            if missing:
                print(f"⚠ 以下能力没匹配到工具：{', '.join(missing)}", flush=True)
            if failed:
                print("提示：失败的 server 可以用 MCP_SKIP 临时跳过，或用 HTTPS_PROXY 指定代理", flush=True)

        yield box
