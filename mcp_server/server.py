"""自研 MCP Server：只提供一个工具 —— calculate_budget。

最短的 MCP server 大概就这样三步：
    1. 造一个 FastMCP 实例
    2. 用 @mcp.tool 把普通函数挂上去（docstring 会变成工具描述）
    3. run() 默认就是 stdio 传输

关于用哪个 FastMCP：
    优先用独立的 `fastmcp` 包（它随 langchain[mcp] 一起装好，API 稳定）。
    如果你装的是 mcp 1.x，退回 `mcp.server.fastmcp`。
    **不要**用 mcp 2.x 的 `mcp.server.mcpserver`：那条路的 API 还在变，
    我们没必要跟着折腾。

启动方式（Agent 侧会自动拉起它，一般不用手动跑）：
    python mcp_server/server.py

单独看它通不通：
    python mcp_server/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让「python mcp_server/server.py」和「python -m mcp_server.server」都能跑
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

try:
    from . import budget_core
except ImportError:  # 脚本方式运行
    import budget_core  # type: ignore[no-redef]


def _force_utf8() -> None:
    """子进程的 stderr 默认跟随系统代码页（Windows 上是 GBK），
    中文报错会被上层当成乱码，这里统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def build_server():
    """构建 FastMCP server。把 import 放进函数里，方便给出可读的报错。"""
    try:
        from fastmcp import FastMCP  # 独立包，推荐
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP  # mcp 1.x 内置版
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "找不到 FastMCP。请执行：pip install \"fastmcp>=2.0\"\n"
                f"原始错误：{exc}"
            ) from exc

    mcp = FastMCP("travel-budget")

    @mcp.tool
    def calculate_budget(items: str) -> str:
        """解析消费清单并核算总金额。

        items 的格式是「项目:金额」用逗号分隔，例如：
            "高铁:1460,门票:640,酒店:600*2"

        返回 JSON，包含：分项明细、合计 total、建议机动金 suggested_buffer、
        总计 grand_total，以及解析失败的片段 warnings。
        """
        return budget_core.calculate_budget(items)

    return mcp


def main() -> None:
    _force_utf8()
    build_server().run()  # 默认 stdio 传输


if __name__ == "__main__":
    main()
