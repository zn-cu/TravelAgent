"""P0 环境自检：先把「外部世界」确认好，再谈 Agent。

    python check_env.py                # 全量检查（含三个 MCP 握手）
    python check_env.py --budget-only  # 只测自研预算工具，不需要任何依赖

它回答三个问题：
1. 版本对不对（Python / Node）
2. 依赖装了没
3. 三个 MCP 连不连得上、**真实工具名叫什么**  ← 最重要的一项

为什么要单独写个脚本：MCP 连不上时，Agent 的报错会很隐晦（工具不存在、超时、空结果）。
先在这里把问题定位到具体某个 server，能省掉大量瞎猜。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def mark(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(' — ' + detail) if detail else ''}")
    return ok


def section(title: str) -> None:
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------- #
# 1. 版本
# --------------------------------------------------------------------------- #
def check_versions() -> bool:
    section("版本")
    ok = mark(sys.version_info >= (3, 10), f"Python {platform.python_version()}", "需要 ≥ 3.10")

    node = shutil.which("node")
    if not node:
        ok = mark(False, "Node.js", "没找到 node，12306 MCP 跑不了；请装 Node ≥ 22")
    else:
        try:
            raw = subprocess.run(
                [node, "-v"], capture_output=True, text=True, timeout=15
            ).stdout.strip()
            major = int(raw.lstrip("v").split(".")[0])
            ok = mark(major >= 22, f"Node.js {raw}", "需要 ≥ 22") and ok
        except (subprocess.SubprocessError, ValueError) as exc:
            ok = mark(False, "Node.js", f"版本读不出来：{exc}")

    npx = shutil.which("npx.cmd") or shutil.which("npx")
    ok = mark(bool(npx), "npx", npx or "没找到 npx") and ok
    return ok


# --------------------------------------------------------------------------- #
# 2. 依赖
# --------------------------------------------------------------------------- #
def _has_module(name: str) -> bool:
    """判断模块是否可用。

    注意：find_spec("langchain.mcp") 在父包 langchain 都没装时会**抛异常**而不是返回 None，
    所以必须包起来 —— 这个坑踩过一次。
    """
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def check_deps() -> bool:
    section("Python 依赖")
    modules = [
        ("deepagents", "deepagents>=0.7", "Agent 编排"),
        ("langchain", "langchain", "工具与中间件"),
        ("langchain.mcp", "langchain[mcp]>=1.4.0", "MCP 客户端"),
        ("fastmcp", "fastmcp>=2.0", "自研 MCP server"),
        ("langchain_deepseek", "langchain-deepseek", "DeepSeek 模型（用别的模型可忽略）"),
        ("dotenv", "python-dotenv", "读 .env（可选）"),
        ("markdown", "markdown>=3.5", "导出 PDF 需要（MD → HTML）"),
    ]
    ok = True
    for module, package, purpose in modules:
        found = _has_module(module)
        ok = mark(found, f"{module}", purpose if not found else "") and ok
    if not ok:
        print("\n  安装命令（用 uv 的话）：")
        print(f'    uv pip install --python "{sys.executable}" -r requirements.txt')
        print("  用 pip 的话：")
        print(f'    "{sys.executable}" -m pip install -r requirements.txt')
        print("  注意：一定要装到「正在跑 check_env.py 的这个解释器」里，")
        print("        否则会出现「明明装了却说没有」的情况。")
    return ok


# --------------------------------------------------------------------------- #
# 3. 配置
# --------------------------------------------------------------------------- #
def check_config() -> bool:
    section("配置")
    from travel_agent import config

    ok = True
    print(f"  模型：{config.MODEL}")
    ok = mark(bool(config.AMAP_MAPS_API_KEY), "AMAP_MAPS_API_KEY", "缺了景点推荐官会没工具") and ok

    problems = config.check_env()
    if problems:
        for problem in problems:
            mark(False, "配置项", problem)
        ok = False

    servers = config.mcp_servers()
    print(f"  MCP server：{', '.join(servers)}")
    return ok


# --------------------------------------------------------------------------- #
# 4. MCP 握手（核心）
# --------------------------------------------------------------------------- #
async def check_mcp(skip: list[str] | None = None, timeout: float | None = None) -> bool:
    section("MCP 握手")
    try:
        from travel_agent.mcp_gateway import open_toolbox
    except ImportError as exc:
        mark(False, "跳过 MCP 检查", str(exc))
        return False

    try:
        async with open_toolbox(verbose=True, timeout=timeout, skip=skip) as box:
            print("\n  工具清单：")
            for tool in box.all_tools:
                detail = (tool.description or "").strip().splitlines()
                print(f"    - {tool.name:<44} {detail[0][:46] if detail else ''}")

            missing = [c for c, names in box.capabilities.items() if not names]
            if missing:
                print(f"\n  这些能力没匹配到工具：{', '.join(missing)}")
                print("  处理办法：把上面打印出的真实工具名，补进 config.CAPABILITIES 里")

            ok = not box.failed and not missing
            if box.failed:
                mark(False, "部分 server 未连上", "；".join(f"{k}: {v}" for k, v in box.failed.items()))
            return ok
    except SystemExit as exc:
        mark(False, "MCP 连接失败", str(exc))
        return False
    except Exception as exc:  # noqa: BLE001 - 这里就是要抓到一切并告诉人
        mark(False, "MCP 连接失败", f"{type(exc).__name__}: {exc}")
        print("\n  常见原因：")
        print("  1) 网络不通 / 需要代理 → 高德是远程 HTTP，需设 HTTPS_PROXY")
        print("  2) AMAP_MAPS_API_KEY 无效 → 去 https://console.amap.com/dev/key 核对")
        print("  3) Node 版本太低 → 12306 MCP 需要 Node ≥ 22")
        print("  4) 某个 server 卡住 → 用 --skip amap 或 --skip rail12306 逐个排除")
        return False


# --------------------------------------------------------------------------- #
# 5. 自研预算工具
# --------------------------------------------------------------------------- #
def check_budget() -> bool:
    section("自研预算工具（不依赖任何第三方库）")
    from mcp_server.budget_core import calculate_budget

    import json

    payload = json.loads(calculate_budget("高铁:1460,门票:640,酒店:600*2"))
    if not payload.get("ok"):
        return mark(False, "calculate_budget", json.dumps(payload, ensure_ascii=False))
    print(f"  输入：高铁:1460,门票:640,酒店:600*2")
    print(f"  合计：{payload['total']}  机动金：{payload['suggested_buffer']}  总计：{payload['grand_total']}")
    return mark(True, "calculate_budget")


# --------------------------------------------------------------------------- #
# 5. 可选能力：PDF 引擎
# --------------------------------------------------------------------------- #
def check_pdf() -> tuple[bool, str]:
    """返回 (是否可用, 说明)。PDF 是可选能力，不参与总闸判定。"""
    section("导出 PDF（可选）")
    from travel_agent.pdf import available_engines

    engines = available_engines()
    if not engines:
        mark(False, "PDF 引擎", "没装引擎，导出 PDF 会退回浏览器打印")
        print("  想启用后端 PDF，任选一种：")
        print('    跨平台推荐  pip install markdown weasyprint   （Linux 另需 libpango / fonts-noto-cjk）')
        print('    纯 wheel    pip install markdown xhtml2pdf')
        print('    Windows     pip install markdown pywin32     （需已装 Microsoft Word）')
        return False, "无引擎"

    mark(True, "PDF 引擎", ", ".join(engines))
    return True, ", ".join(engines)


# --------------------------------------------------------------------------- #
def main() -> int:
    force_utf8()
    parser = argparse.ArgumentParser(description="环境自检")
    parser.add_argument("--budget-only", action="store_true", help="只测自研预算工具")
    parser.add_argument(
        "--skip", action="append", default=None, help="跳过某个 MCP server，可重复：--skip amap"
    )
    parser.add_argument("--timeout", type=float, help="单个 MCP server 的连接超时（秒）")
    args = parser.parse_args()

    if args.budget_only:
        return 0 if check_budget() else 1

    results = {
        "版本": check_versions(),
        "依赖": check_deps(),
        "配置": check_config(),
        "预算工具": check_budget(),
    }
    if all(results.values()):
        results["MCP"] = asyncio.run(check_mcp(skip=args.skip, timeout=args.timeout))

    pdf_ok, pdf_detail = check_pdf()

    print("\n=== 汇总 ===")
    for name, ok in results.items():
        mark(ok, name)
    mark(pdf_ok, "PDF（可选）", pdf_detail)

    if all(results.values()):
        print("\n一切就绪。下一步：")
        print("  Web ：python -m travel_agent.server   → http://127.0.0.1:8000")
        print('  CLI ：python -m travel_agent.cli "我想去成都玩3天"')
        return 0
    print("\n先修上面的 FAIL 项，再跑 Agent。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
