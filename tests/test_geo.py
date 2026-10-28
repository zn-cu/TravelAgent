"""高德 https 链接生成的单元测试（纯标准库）。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent.geo import build_payload, marker_url, parse_spots  # noqa: E402


class TestParseSpots(unittest.TestCase):
    def test_single(self):
        points, problems = parse_spots("熊猫基地|104.062,30.671")
        self.assertEqual(problems, [])
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["name"], "熊猫基地")
        self.assertAlmostEqual(points[0]["lng"], 104.062)

    def test_multiple_and_spaces(self):
        points, _ = parse_spots(" A | 104.0 , 30.0 ; B | 105.0,31.0 ")
        self.assertEqual([p["name"] for p in points], ["A", "B"])

    def test_missing_coord_is_reported(self):
        points, problems = parse_spots("熊猫基地|104.0")
        self.assertEqual(points, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("格式不对", problems[0])

    def test_non_numeric(self):
        points, problems = parse_spots("熊猫基地|东经104,北纬30")
        self.assertEqual(points, [])
        self.assertIn("不是数字", problems[0])

    def test_out_of_range(self):
        points, problems = parse_spots("错点|204.0,30.0")
        self.assertEqual(points, [])
        self.assertIn("超出范围", problems[0])

    def test_partial_success(self):
        """一条坏点位不该拖累其余好点位。"""
        points, problems = parse_spots("好|104.0,30.0;坏|abc;也好|105.0,31.0")
        self.assertEqual(len(points), 2)
        self.assertEqual(len(problems), 1)


class TestMarkerUrl(unittest.TestCase):
    def test_shape(self):
        url = marker_url("熊猫基地", 104.062, 30.671)
        self.assertTrue(url.startswith("https://uri.amap.com/marker?"))
        self.assertIn("position=104.062,30.671", url)
        self.assertIn("coordinate=gaode", url)
        self.assertIn("callnative=1", url)
        self.assertIn("src=travel-agent", url)

    def test_name_is_url_encoded(self):
        url = marker_url("宽窄巷子", 104.0, 30.0)
        self.assertNotIn("宽窄巷子", url)          # 中文必须编码，否则某些浏览器会截断
        self.assertIn("%E5%AE%BD%E7%AA%84", url)


class TestBuildPayload(unittest.TestCase):
    def test_ok(self):
        payload = build_payload("熊猫基地|104.062,30.671;宽窄巷子|104.055,30.669")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["links"]), 2)
        self.assertTrue(payload["links"][0]["url"].startswith("https://"))

    def test_empty(self):
        payload = build_payload("")
        self.assertFalse(payload["ok"])
        self.assertIn("error", payload)

    def test_all_invalid(self):
        payload = build_payload("坏|abc")
        self.assertFalse(payload["ok"])
        self.assertEqual(len(payload["warnings"]), 1)

    def test_warnings_survive_partial_failure(self):
        payload = build_payload("好|104.0,30.0;坏|abc")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["warnings"]), 1)


if __name__ == "__main__":
    unittest.main()
