"""CEO 的组装。

`create_deep_agent` 一个调用就自动拿到了：待办清单（write_todos）、虚拟文件系统、
子智能体分派（task）、上下文摘要、Skills 注入、HITL 中断机制。
我们要做的只是把「专家团」和「两条纪律」传进去。
"""

from __future__ import annotations

import json
import time

from langchain.tools import tool
from langgraph.checkpoint.memory import MemorySaver

from . import config
from .geo import build_payload
from .middleware import build_middlewares
from .publish import save_proposal
from .subagents import build_subagents

# --------------------------------------------------------------------------- #
# CEO 的行为规范
# --------------------------------------------------------------------------- #
CEO_PROMPT = """你是旅游公司的项目经理（Travel Manager），手下有三个专家：
- attraction-specialist  景点推荐官
- transport-planner      出行规划师
- budget-keeper          财务管家

严格按照下面的流程工作：

1. 拆需求。抓住四件事：目的地、天数、出发地、人数与预算。
   缺关键信息（尤其是出发地和天数）就先问用户一句，这一轮不要派活。
2. 用 write_todos 写出计划，让用户看得见你在干什么。
3. 先把「景点」和「交通」两个任务派出去（它们互不依赖）。
   派活时把已知信息写全：目的地、天数、出发地、人数、用户偏好。
4. 等两者结果都回来，再派财务管家。派活时必须把交通和门票的具体金额告诉它，
   让它用 calculate_budget 核算，不要让它自己估。
5. 读 skills/travel-proposal/SKILL.md，按里面的模板汇总成一份《旅游策划书》。
6. 生成行程地图，做两件事：
   ① 调用 build_amap_links，传入「名称|经纬度」列表（经纬度抄景点推荐官报告里的 location，
      格式 `104.062,30.671`），拿到每个景点的 https 链接——这是**给手机点的主链接**。
   ② 调用 maps_schema_personal_map 生成"专属地图"唤端链接（amapuri:// 开头），作为附加信息。
   两个都放进策划书的「六、地图」一节。任何一个失败就写「生成失败：原因」，不要伪造链接，
   也不要因此中断整个流程。
7. 落盘之前必须调用 confirm_itinerary 交用户确认。
8. 用户确认后，调用 publish_proposal 把**完整的策划书正文**落盘。
   注意：不要用 write_file 写策划书，也不要把"已保存"这类寒暄当成正文传进去。
   如果用户还没给全信息（你这一轮只是在追问），**根本不要调用 publish_proposal**。

铁律：
- 不许自己编景点名、车次、票价、门票。所有事实必须来自专家返回的结果。
- 专家没给出的数字，不要出现在策划书里；缺了就写「待补充」。
- 用户否决或要求修改时，按用户意见改，不要整条流程重跑一遍。
- 三轮对话内必须产出一版可执行的方案。
"""


# --------------------------------------------------------------------------- #
# CEO 自己的三个工具（HITL 的闸口挂在 confirm_itinerary / book_hold 上）
# --------------------------------------------------------------------------- #
@tool
def confirm_itinerary(summary: str) -> str:
    """把整理好的方案交给用户确认。落盘之前必须调用它，这是唯一必经的人工关卡。

    summary 必须是**给人看的方案要点**，不要写「测试」「如上」这类占位。请按这个格式：

        目的地：成都 ｜ 天数：3 天 2 晚 ｜ 人数：2 人
        出发地：西安 ｜ 日期：11-04 ～ 11-06
        交通：G3437 西安北 07:33 → 成都东 11:06，二等座 ¥730×2（门到门 5.0 小时）
        行程：D1 熊猫基地+宽窄巷子 / D2 杜甫草堂+四川博物院 / D3 武侯祠+返程
        预算：合计 ¥3300 + 机动金 ¥330 = ¥3630，人均 ¥1815（预算 ¥3000 → 超 ¥630）

    用户会在界面上直接读这段文字来决策，所以每一项都必须具体、可核对。
    """
    return f"用户已确认以下方案，可以落盘：{summary}"


@tool
def book_hold(order_summary: str, total_price: float) -> str:
    """模拟锁单（不产生真实交易，只生成一个占位订单号）。

    total_price 超过预算阈值时会先请用户确认，确认后才走到这里。
    order_summary 同样要写人话，例如「成都 3 天 2 人，交通+门票 ¥2052」。
    """
    return json.dumps(
        {
            "ok": True,
            "order_id": f"HOLD{int(time.time())}",
            "total_price": total_price,
            "summary": order_summary,
            "note": "模拟锁单，未发生真实支付。",
        },
        ensure_ascii=False,
    )


@tool
def build_amap_links(spots: str) -> str:
    """把「名称|经纬度」列表拼成高德 https 链接（手机上点开就能唤起高德 App）。

    spots 的格式：`"熊猫基地|104.062,30.671;宽窄巷子|104.055,30.669"`
    经纬度从景点推荐官返回的 location 字段原样抄过来，顺序是「经度,纬度」。

    为什么需要它：高德 MCP 的「专属地图」返回的是 `amapuri://` 私有协议，
    桌面浏览器和微信里点不开。这个工具生成的是标准 https 链接（高德官方 URI API），
    两边都能点，手机端会直接唤起 App。
    """
    # 解析与 URL 拼装放在 geo.py（纯标准库），这样能离线单测
    return json.dumps(build_payload(spots), ensure_ascii=False, indent=2)


def over_budget(request) -> bool:
    """条件中断的判定函数：只有总价超过 BUDGET_LIMIT 才停下来问人。

    平时不触发，所以不会打断正常流程 —— 这就是「条件中断」的价值。
    """
    args = (getattr(request, "tool_call", {}) or {}).get("args", {})
    try:
        return float(args.get("total_price", 0)) > config.BUDGET_LIMIT
    except (TypeError, ValueError):
        return True  # 参数看不懂就保守一点，宁可多问一次


@tool
def publish_proposal(markdown: str) -> str:
    """把最终《旅游策划书》落盘成 markdown 文件。

    **只有在用户确认方案之后才能调用**，而且 markdown 必须是**完整的策划书正文**
    （从标题「# 成都 3 天…」到最后的风险提示），不是寒暄、不是追问、不是一句总结。

    如果你此刻只是在向用户确认信息（比如还没拿到目的地/天数），
    **不要调用这个工具**，直接把问题回复给用户即可 —— 调用会失败，因为内容太短。
    """
    # 落盘与校验放在 publish.py（纯标准库），可离线单测
    return json.dumps(save_proposal(markdown, config.OUTPUT_DIR), ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
def build_ceo_agent(box):
    """box 是 mcp_gateway.ToolBox，里面装着本次会话的 MCP 工具。"""
    from deepagents import create_deep_agent

    # FilesystemBackend 让 skills/ 直接从磁盘读，改 SKILL.md 立刻生效。
    # virtual_mode=True 把文件操作限制在项目目录内，避免 Agent 乱写别处。
    try:
        from deepagents.backends import FilesystemBackend
    except ImportError:  # pragma: no cover - 兼容不同版本
        from deepagents.backends.filesystem import FilesystemBackend

    backend = FilesystemBackend(root_dir=str(config.PROJECT_ROOT), virtual_mode=True)

    return create_deep_agent(
        model=config.MODEL,
        system_prompt=CEO_PROMPT,
        # 前两个是 HITL 的闸口，publish 负责落盘，最后两个是行程地图
        tools=[
            confirm_itinerary,
            book_hold,
            publish_proposal,
            build_amap_links,
            *box.tools("map"),
        ],
        subagents=build_subagents(box),
        skills=[config.SKILLS_SOURCE],
        backend=backend,
        middleware=build_middlewares(),
        interrupt_on={
            # 闸口一：每次都要人工确认
            "confirm_itinerary": {"allowed_decisions": ["approve", "edit", "reject"]},
            # 闸口二：只有超预算才停
            "book_hold": {"allowed_decisions": ["approve", "reject"], "when": over_budget},
        },
        checkpointer=MemorySaver(),  # HITL 必需，否则中断后无法恢复
        name="travel-manager",
    )
