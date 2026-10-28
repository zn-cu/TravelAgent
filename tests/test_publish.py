"""策划书落盘的单元测试（纯标准库）。"""

from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.publish import MIN_CHARS, save_proposal  # noqa: E402

GOOD = "# 成都 3 天 2 人 · 旅游策划书\n\n## 一、行程概览\n" + "成都三日游的详细安排。" * 20


class TestSaveProposal(unittest.TestCase):
    def setUp(self):
        # 不用 tempfile.TemporaryDirectory：它在 Windows 上会把目录 chmod 成 0700，
        # 在受限环境里反而导致后续写入被拒。这里自己建一个普通目录，用完删掉。
        self.dir = Path(__file__).resolve().parent / f"_publish_tmp_{uuid.uuid4().hex[:8]}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_rejects_short_content(self):
        """追问、寒暄这种短文本绝不能被当成策划书存下来。"""
        result = save_proposal("您好，请告诉我目的地、出发地和天数。", self.dir)
        self.assertFalse(result["ok"])
        self.assertIn("不像一份完整策划书", result["error"])
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_rejects_empty(self):
        self.assertFalse(save_proposal("   ", self.dir)["ok"])

    def test_saves_and_reads_back(self):
        result = save_proposal(GOOD, self.dir)
        self.assertTrue(result["ok"])
        self.assertTrue(result["file"].endswith(".md"))

        saved = (self.dir / result["file"]).read_text(encoding="utf-8")
        self.assertIn("成都 3 天 2 人", saved)
        self.assertTrue(saved.endswith("\n"))
        self.assertEqual(result["chars"], len(GOOD.strip()))

    def test_creates_directory(self):
        target = self.dir / "nested" / "output"
        result = save_proposal(GOOD, target)
        self.assertTrue(result["ok"])
        self.assertTrue((target / result["file"]).is_file())

    def test_same_second_does_not_overwrite(self):
        """同一秒内发布两份，第二份要自动加后缀，不能覆盖第一份。"""
        first = save_proposal(GOOD, self.dir, when=1_700_000_000)
        second = save_proposal(GOOD + "\n补充说明。\n" * 10, self.dir, when=1_700_000_000)
        self.assertNotEqual(first["file"], second["file"])
        self.assertEqual(len(list(self.dir.glob("*.md"))), 2)

    def test_counts_sections(self):
        result = save_proposal(GOOD, self.dir)
        self.assertGreaterEqual(result["sections"], 1)

    def test_min_chars_is_reasonable(self):
        """阈值本身也要有测试：太低会把寒暄放进来，太高会卡住正常短方案。"""
        self.assertGreaterEqual(MIN_CHARS, 80)
        self.assertLessEqual(MIN_CHARS, 300)


if __name__ == "__main__":
    unittest.main()
