"""三个专家子智能体。

一个「专家」其实就是三样东西：一段 system_prompt + 几个工具 + 若干 skill。

deepagents 的两条规则要知道：
1. 子智能体**不会继承**主智能体的 skills，必须在每个 subagent 里单独声明。
2. 指定了 `tools` 就会**完全覆盖**继承来的工具，所以每个专家拿到的工具是干净的。
"""

from __future__ import annotations

import json
from math import asin, cos, radians, sin, sqrt

from langchain.tools import tool

from . import config

# --------------------------------------------------------------------------- #
# 本地小工具：飞机估价
# --------------------------------------------------------------------------- #
# 飞机没有免费可用的 MCP（高德只管地图，12306 只管火车，飞猪要商务授权），
# 与其让模型自己编价格，不如给一个确定性的估算公式。
_CITY_COORDS = {
    "北京": (39.90, 116.41), "上海": (31.23, 121.47), "广州": (23.13, 113.26),
    "深圳": (22.54, 114.06), "成都": (30.57, 104.07), "重庆": (29.56, 106.55),
    "西安": (34.34, 108.94), "杭州": (30.27, 120.16), "南京": (32.06, 118.80),
    "厦门": (24.48, 118.09), "三亚": (18.25, 109.51), "昆明": (25.04, 102.71),
    "青岛": (36.07, 120.38), "武汉": (30.59, 114.31), "长沙": (28.23, 112.94),
}


def _distance_km(a: str, b: str) -> float:
    """球面距离；不认识的城填按 1200km 处理，保证结果稳定可复现。"""
    if a not in _CITY_COORDS or b not in _CITY_COORDS:
        return 1200.0
    lat1, lon1 = (radians(x) for x in _CITY_COORDS[a])
    lat2, lon2 = (radians(x) for x in _CITY_COORDS[b])
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


@tool
def estimate_flight(origin: str, dest: str) -> str:
    """估算两地之间的飞机耗时与票价区间（模拟数据，用来和高铁比价）。"""
    if not origin or not dest:
        return json.dumps({"ok": False, "error": "origin 和 dest 都不能为空"}, ensure_ascii=False)

    distance = _distance_km(origin.strip(), dest.strip())
    hours = round(distance / 800 + 0.5, 1)          # 巡航时间
    price = max(380, round(distance * 0.6 / 10) * 10) + 130  # 票价 + 机建燃油
    return json.dumps(
        {
            "ok": True,
            "tool": "estimate_flight",
            "origin": origin,
            "dest": dest,
            "distance_km": int(distance),
            "flight_hours": hours,
            "door_to_door_hours": round(hours + 3.0, 1),  # 含往返机场与安检
            "price_range": [max(380, int(price * 0.75)), int(price * 1.15)],
            "note": "模拟估算值，实际票价以航司官网为准。",
        },
        ensure_ascii=False,
        indent=2,
    )


# --------------------------------------------------------------------------- #
# 三个专家
# --------------------------------------------------------------------------- #
def build_subagents(box) -> list[dict]:
    """按能力给三个专家分配工具。box 是 mcp_gateway.ToolBox。"""

    attraction = {
        "name": "attraction-specialist",
        "description": (
            "景点推荐官。负责搜索指定城市的真实景点（名称、亮点、建议时长、门票、避坑提示），"
            "并按天给出游玩顺序。凡是涉及「有什么好玩的/去哪逛」都派给它。"
        ),
        "system_prompt": (
            "你是景点推荐官。你只负责推荐景点，不关心怎么去、也不关心花多少钱。\n\n"
            "工作方式：\n"
            "1. 先读 skills/attraction-scout/SKILL.md，按里面的流程做。\n"
            "2. 用 maps_text_search 搜索，关键词用「城市 + 景点」「城市 + 必去」这类组合；\n"
            "   需要门票/开放时间/评分时用 maps_search_detail 补。\n"
            "3. 用 maps_weather 看那几天的天气，决定哪些天安排室外、哪些安排室内。\n"
            "4. 只推荐你真正查到的景点。查不到就直说查不到，绝不编造景点名、门票或开放时间。\n\n"
            "输出：先给一张 Markdown 表格（景点 | 亮点 | 建议时长 | 门票 | 区域 | 避坑），"
            "再给按天分配的游玩顺序，每天 2-3 个景点，并说明为什么这么排（顺路/冷热搭配）。"
        ),
        "tools": box.tools("poi", "poi_detail", "weather"),
        "skills": [config.SKILLS_SOURCE],
    }

    transport = {
        "name": "transport-planner",
        "description": (
            "出行规划师。负责查真实火车班次票价、对比飞机与高铁、给出最便捷的交通方案。"
            "凡是涉及「怎么去/坐什么/要多久/票价多少」都派给它。"
        ),
        "system_prompt": (
            "你是出行规划师。你只解决交通问题，不评价景点、也不算总花费。\n\n"
            "工作方式：\n"
            "1. 先读 skills/transport-planner/SKILL.md，按里面的流程做。\n"
            "2. 查火车前先用 get-stations-code-in-city 把城市名换成车站码；\n"
            "   用 get-current-date 确认今天的日期，避免查错时间。\n"
            "3. 用 get-tickets 查直达车次；没有合适的直达就用 get-interline-tickets 查中转。\n"
            "4. 飞机用 estimate_flight 估算，并在结果里标注那是估算值。\n"
            "5. 12306 有频率限制：一次行程最多查 4 次（往返各 2 次），不要反复查同一个区间。\n\n"
            "对比口径统一用「门到门耗时」（火车另加 1.5 小时市内接驳，飞机另加 3 小时）。\n"
            "输出：一张对比表（方式 | 班次 | 出发-到达 | 历时 | 门到门 | 票价 | 余票/备注），"
            "然后给一个推荐方案 + 一个备选，并说明推荐理由。"
        ),
        "tools": box.tools(
            "station", "rail", "rail_transfer", "today", "geo", "transit", "distance"
        )
        + [estimate_flight],
        "skills": [config.SKILLS_SOURCE],
    }

    budget = {
        "name": "budget-keeper",
        "description": (
            "财务管家。负责把各项开销汇总核算，给出分项表、总计、人均和建议机动金。"
            "凡是涉及「总共多少钱/超没超预算」都派给它。"
        ),
        "system_prompt": (
            "你是财务管家。你只做一件事：把账算准。\n\n"
            "铁律：\n"
            "1. 必须调用 calculate_budget，绝不许自己心算。\n"
            "2. 金额只能来自上游给的结果或用户明说的数字。缺哪一项就明确说「缺 X 的费用」，"
            "绝对不许估算、不许编。\n"
            "3. 调用前把清单拼成「项目:金额」的字符串，多人分摊用「金额*人数」，"
            "例如：\"高铁:1460,门票:640,酒店:600*2\"。\n\n"
            "输出：分项表（项目 | 单价 | 数量 | 小计）+ 合计 + 10% 机动金 + 总计 + 人均，"
            "最后明确说「是否超预算、超了多少或还剩多少」。"
        ),
        "tools": box.tools("budget"),
        "skills": [config.SKILLS_SOURCE],
    }

    return [attraction, transport, budget]
