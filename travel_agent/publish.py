"""把最终策划书落盘（纯标准库，可离线单测）。

为什么单独一个模块：
    之前的做法是"把最后一条 AI 文本存成文件"，结果用户追问一句
    「请告诉我目的地/出发地/天数」，也会被存成一份"策划书"——
    下载下来是段寒暄，非常荒谬。

    现在改成**显式发布**：CEO 必须调用 publish_proposal 工具才算产出了策划书。
    这样「追问」和「成稿」在代码层面就是两件事，前端也不会给出假的下载链接。
"""

from __future__ import annotations

import time
from pathlib import Path

#: 少于这个字数基本可以断定不是策划书（追问、寒暄都远低于它）
MIN_CHARS = 120


def _unique_path(output_dir: Path, stamp: str) -> Path:
    """同一秒内多次发布也不能互相覆盖。"""
    path = output_dir / f"策划书-{stamp}.md"
    index = 1
    while path.exists():
        path = output_dir / f"策划书-{stamp}-{index}.md"
        index += 1
    return path


def save_proposal(markdown: str, output_dir: Path, when: float | None = None) -> dict:
    """写文件并返回结果字典。

    返回 {"ok": True, "file": 文件名, "chars": 字数, ...}
    或     {"ok": False, "error": 原因}
    """
    text = (markdown or "").strip()
    if not text:
        return {"ok": False, "error": "内容是空的，没有可保存的策划书"}

    if len(text) < MIN_CHARS:
        return {
            "ok": False,
            "error": (
                f"内容只有 {len(text)} 字，不像一份完整策划书（至少 {MIN_CHARS} 字）。"
                "如果你只是想向用户确认信息，不要调用这个工具，直接回复即可。"
            ),
            "chars": len(text),
        }

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%m%d-%H%M%S", time.localtime(when if when is not None else time.time()))
    path = _unique_path(output_dir, stamp)
    path.write_text(text + "\n", encoding="utf-8")

    return {
        "ok": True,
        "file": path.name,
        "chars": len(text),
        "sections": text.count("\n## "),
        "note": "已保存。前端会据此给出 Markdown 下载和 PDF 导出。",
    }
