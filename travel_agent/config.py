"""集中配置：模型、三个 MCP Server、能力映射、阈值。

原则：所有"以后可能会改的东西"都放这个文件，其他文件里不出现魔法数字。
改参数只改 `.env` 或本文件顶部的常量。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """极简 .env 读取器（纯标准库，20 行）。

    为什么自己写而不是只依赖 python-dotenv：
    dotenv 没装时 .env 会被**静默忽略**，表现就是「明明填了 key，程序说没有」，
    这种失败最难查。宁可多这 20 行，也不留一个静默失败的坑。

    规则：不覆盖已存在的系统环境变量（系统环境变量优先级更高）。
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(ENV_FILE)

try:  # 如果装了 python-dotenv，再兜一层（它默认不覆盖已有变量，行为一致）
    from dotenv import load_dotenv

    load_dotenv(ENV_FILE)
except ImportError:  # pragma: no cover
    pass

SKILLS_SOURCE = "skills/"  # 相对 backend root，必须用正斜杠
SKILLS_DIR = PROJECT_ROOT / "skills"
OUTPUT_DIR = PROJECT_ROOT / "output"
LOGS_DIR = PROJECT_ROOT / "logs"
TRACE_FILE = LOGS_DIR / "trace.jsonl"
BUDGET_SERVER = PROJECT_ROOT / "mcp_server" / "server.py"

# --------------------------------------------------------------------------- #
# 模型与密钥
# --------------------------------------------------------------------------- #
MODEL = os.getenv("TRAVEL_MODEL", "deepseek:deepseek-chat")
AMAP_MAPS_API_KEY = os.getenv("AMAP_MAPS_API_KEY", "").strip()

# --------------------------------------------------------------------------- #
# 阈值（都能在 .env 里改）
# --------------------------------------------------------------------------- #
BUDGET_LIMIT = float(os.getenv("BUDGET_LIMIT", "5000"))
RAIL_MIN_INTERVAL = float(os.getenv("RAIL_MIN_INTERVAL", "3.0"))
RAIL_MAX_CALLS = int(os.getenv("RAIL_MAX_CALLS", "20"))

# MCP 连接：单个 server 的超时时间（秒），以及要临时跳过的 server 名单。
# npx 首次要从网上拉 12306-mcp 包，所以默认给 90 秒；网络慢就调大。
MCP_TIMEOUT = float(os.getenv("MCP_TIMEOUT", "90"))
MCP_SKIP = [name.strip() for name in os.getenv("MCP_SKIP", "").split(",") if name.strip()]

# --------------------------------------------------------------------------- #
# 能力映射
# --------------------------------------------------------------------------- #
# 为什么不直接写工具名：第三方 MCP 的工具名会变（12306 就有好几种实现，
# 有的叫 get-tickets，有的叫 get_tickets）。所以给每个"能力"列一串候选名，
# 启动时用 tools/list 的真实结果去匹配，匹配不到就 warn 而不是崩。
CAPABILITIES: dict[str, list[str]] = {
    "poi": ["maps_text_search", "maps_around_search"],
    "poi_detail": ["maps_search_detail"],
    "weather": ["maps_weather"],
    # 高德这版只有「经纬度版」的公交规划，没有 by_address 版，
    # 所以必须先 maps_geo 把地址转成坐标。geo 这个能力就是给它用的。
    "geo": ["maps_geo", "maps_regeocode"],
    "transit": [
        "maps_direction_transit_integrated_by_address",
        "maps_direction_transit_integrated",
    ],
    "distance": ["maps_distance"],
    "station": ["get-stations-code-in-city"],
    "rail": ["get-tickets", "get_tickets"],
    "rail_transfer": ["get-interline-tickets", "get_interline_tickets"],
    "today": ["get-current-date"],
    "budget": ["calculate_budget"],
    # 行程专属地图：把点位按行程顺序排好，生成高德 App 的唤端链接。
    # 它是"渲染器"不是"数据源"，只有 CEO 手上有完整行程，所以只给 CEO 用。
    "map": ["maps_schema_personal_map"],
}


# --------------------------------------------------------------------------- #
# MCP Server 配置
# --------------------------------------------------------------------------- #
def _npx_server(package: str) -> dict:
    """生成一个用 npx 启动的 stdio MCP server 配置。

    优先用「已全局安装的可执行文件」：服务器上 `npm i -g 12306-mcp` 之后启动只需几十毫秒，
    而且不依赖运行时联网（`npx -y` 每次都要校验/下载，慢且在离线环境会失败）。

    Windows 上有个必踩的坑：npx 其实是 `.cmd` 批处理，CreateProcess 不能直接执行
    批处理文件，直接把它当 command 会报"找不到命令"。统一走 `cmd /c npx` 最稳。
    """
    installed = shutil.which(package)
    if installed and (os.name != "nt" or installed.lower().endswith(".exe")):
        return {"command": installed, "args": []}

    if os.name == "nt":
        return {"command": "cmd", "args": ["/c", "npx", "-y", package]}
    return {"command": "npx", "args": ["-y", package]}


def mcp_servers() -> dict[str, dict]:
    """三个 MCP server 的配置（claude-desktop 风格 mcpServers 的内容）。"""
    servers: dict[str, dict] = {
        "rail12306": _npx_server("12306-mcp"),
        "budget": {"command": sys.executable, "args": [str(BUDGET_SERVER)]},
    }
    if AMAP_MAPS_API_KEY:
        # 高德官方推荐 Streamable HTTP，不用在本地跑进程
        servers["amap"] = {"url": f"https://mcp.amap.com/mcp?key={AMAP_MAPS_API_KEY}"}
    return servers


# --------------------------------------------------------------------------- #
# 环境自检
# --------------------------------------------------------------------------- #
def check_env() -> list[str]:
    """返回环境问题清单；返回空列表就表示可以开跑。"""
    problems: list[str] = []

    if not AMAP_MAPS_API_KEY:
        problems.append(
            "缺少 AMAP_MAPS_API_KEY（景点推荐官会没有工具可用）：把 .env.example 复制成 .env 并填写"
        )
    if not any(
        os.getenv(k)
        for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY")
    ):
        problems.append("没检测到任何模型 API Key（例如 DEEPSEEK_API_KEY）")
    if not BUDGET_SERVER.exists():
        problems.append(f"找不到预算 MCP server：{BUDGET_SERVER}")
    if not SKILLS_DIR.exists():
        problems.append(f"找不到 skills 目录：{SKILLS_DIR}")
    if not (shutil.which("npx.cmd") or shutil.which("npx")):
        problems.append("找不到 npx（12306 MCP 需要 Node.js ≥ 22）")

    return problems
