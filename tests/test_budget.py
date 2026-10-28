"""预算解析的单元测试（纯标准库，不需要装任何依赖）。

    python -m unittest discover tests -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.budget_core import calculate_budget  # noqa: E402


def parse(text: str) -> dict:
    return json.loads(calculate_budget(text))


class TestCalculateBudget(unittest.TestCase):
    def test_basic(self):
        result = parse("机票:500,门票:200")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 700)
        self.assertEqual(result["item_count"], 2)

    def test_chinese_punctuation(self):
        """全角冒号和中文逗号是最常见的输入形式。"""
        result = parse("机票：500，门票：200")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 700)

    def test_quantity_multiplier(self):
        """金额*数量：门票 200 买 4 张。"""
        result = parse("门票:200*4,酒店:600*2")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 800 + 1200)
        self.assertEqual(result["items"][0]["quantity"], 4)

    def test_space_separator(self):
        """没有冒号的写法也要兼容。"""
        result = parse("机票 500,门票 200")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 700)

    def test_buffer_and_grand_total(self):
        result = parse("机票:1000")
        self.assertEqual(result["total"], 1000)
        self.assertEqual(result["suggested_buffer"], 100)
        self.assertEqual(result["grand_total"], 1100)

    def test_dirty_input_goes_to_warnings(self):
        """解析不了的片段进 warnings，但不影响其它项。"""
        result = parse("机票:500,随便写的,门票:200")
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 700)
        self.assertEqual(len(result["warnings"]), 1)

    def test_empty_input(self):
        result = parse("")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_list_input(self):
        """有些模型会把清单塞成数组。"""
        result = json.loads(calculate_budget(["机票:500", "门票:200"]))
        self.assertEqual(result["total"], 700)

    def test_decimal_amount(self):
        result = parse("门票:99.5*2")
        self.assertEqual(result["total"], 199.0)


if __name__ == "__main__":
    unittest.main()
