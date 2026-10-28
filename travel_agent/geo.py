"""高德链接生成（纯标准库，可离线单测）。

为什么需要它：
    高德 MCP 的「专属地图」返回的是 `amapuri://workInAmap/...`，那是**高德 App 的私有协议**，
    不是网页链接 —— 桌面浏览器、微信里点了没反应，很多人会以为坏了。
    这个模块生成的是高德官方的 URI API 链接（https://uri.amap.com/marker），
    手机浏览器点开可以直接唤起 App，PC 上也能打开网页版。

坐标格式：`经度,纬度`（高德是 GCJ-02，正好和 MCP 返回的一致，不用转换）。
"""

from __future__ import annotations

from typing import Any

MARKER_URL = "https://uri.amap.com/marker"
SOURCE = "travel-agent"


def parse_spots(spots: str) -> tuple[list[dict[str, Any]], list[str]]:
    """把「名称|经度,纬度;名称|经度,纬度」解析成点位列表。

    返回 (点位列表, 问题列表)。问题不抛异常，而是收集起来交给上游说明。
    """
    points: list[dict[str, Any]] = []
    problems: list[str] = []

    for raw in str(spots or "").replace("\n", ";").split(";"):
        item = raw.strip()
        if not item:
            continue

        name, _, coord = item.partition("|")
        name = name.strip() or "未命名地点"
        coord = coord.strip().replace(" ", "")

        parts = coord.split(",")
        if len(parts) != 2:
            problems.append(f"坐标格式不对（应为「经度,纬度」），已跳过：{item}")
            continue
        try:
            lng, lat = float(parts[0]), float(parts[1])
        except ValueError:
            problems.append(f"坐标不是数字，已跳过：{item}")
            continue
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            problems.append(f"坐标超出范围，已跳过：{item}")
            continue

        points.append({"name": name, "lng": lng, "lat": lat})

    return points, problems


def marker_url(name: str, lng: float, lat: float) -> str:
    """单个点位的 https 链接。手机点开会直接唤起高德 App 并定位到该点。"""
    from urllib.parse import quote

    return (
        f"{MARKER_URL}?position={lng},{lat}"
        f"&name={quote(name)}"
        f"&src={SOURCE}&coordinate=gaode&callnative=1"
    )


def build_payload(spots: str) -> dict[str, Any]:
    """给工具调用的完整返回体。"""
    points, problems = parse_spots(spots)
    if not points:
        return {
            "ok": False,
            "error": "没有解析出任何有效点位，格式应为「名称|经度,纬度」，多条用分号分隔",
            "warnings": problems,
        }

    return {
        "ok": True,
        "count": len(points),
        "links": [
            {"name": p["name"], "url": marker_url(p["name"], p["lng"], p["lat"])} for p in points
        ],
        "warnings": problems,
        "note": "https 链接，手机点开会唤起高德 App。",
    }
