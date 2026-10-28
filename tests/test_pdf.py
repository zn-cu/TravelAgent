"""PDF 引擎选择与渲染的单元测试。

不需要真的装 weasyprint/Word：用一个假的 markdown 模块和一个假引擎，
就能把「选择器 + 失败兜底 + 成功判定」这几条逻辑测干净。
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from travel_agent import pdf  # noqa: E402

_original_markdown = None


def setUpModule():
    """装一个假的 markdown 模块，让结果不受本机是否装了 python-markdown 影响。"""
    global _original_markdown
    _original_markdown = sys.modules.get("markdown")

    fake = types.ModuleType("markdown")
    fake.markdown = lambda text, extensions=None: f'<div class="md">{text}</div>'
    sys.modules["markdown"] = fake


def tearDownModule():
    if _original_markdown is None:
        sys.modules.pop("markdown", None)
    else:
        sys.modules["markdown"] = _original_markdown


class TestBuildHtml(unittest.TestCase):
    def setUp(self):
        self.dir = Path(__file__).resolve().parent / "_pdf_tmp"
        self.dir.mkdir(exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_has_print_styles(self):
        html = pdf.build_html("# 成都 3 天\n\n正文内容")
        self.assertIn("@page", html)          # A4 页面设置
        self.assertIn("size: A4", html)
        self.assertIn("Noto Sans CJK SC", html)  # 中文字体栈（PDF 中文变方块就靠它）
        self.assertIn("成都 3 天", html)
        self.assertIn("正文内容", html)

    def test_title_goes_into_head(self):
        html = pdf.build_html("x", title="我的策划书")
        self.assertIn("<title>我的策划书</title>", html)

    def test_empty_text_still_valid(self):
        html = pdf.build_html("")
        self.assertTrue(html.startswith("<!doctype html>"))
        self.assertIn("</html>", html)


class TestEngineSelection(unittest.TestCase):
    def test_available_is_subset_of_known(self):
        self.assertTrue(set(pdf.available_engines()) <= set(pdf._RENDERERS))

    def test_forced_engine(self):
        self.assertEqual(pdf._resolve_engine("word"), ["word"])

    def test_auto_falls_back_to_available(self):
        with mock.patch.object(pdf, "available_engines", return_value=["xhtml2pdf"]):
            self.assertEqual(pdf._resolve_engine("auto"), ["xhtml2pdf"])

    def test_env_var_overrides_auto(self):
        with mock.patch.dict("os.environ", {"PDF_ENGINE": "word"}):
            self.assertEqual(pdf._resolve_engine("auto"), ["word"])


class TestRender(unittest.TestCase):
    def setUp(self):
        self.dir = Path(__file__).resolve().parent / "_pdf_tmp"
        self.dir.mkdir(exist_ok=True)
        self.out = self.dir / "out.pdf"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def test_no_engine_gives_hint(self):
        with mock.patch.object(pdf, "available_engines", return_value=[]):
            result = pdf.render("# 标题", self.out)
        self.assertFalse(result["ok"])
        self.assertIn("没有可用的 PDF 引擎", result["error"])
        self.assertIn("weasyprint", result["hint"])  # 要告诉用户装什么

    def test_unknown_engine_is_reported(self):
        result = pdf.render("# 标题", self.out, engine="不存在的引擎")
        self.assertFalse(result["ok"])
        self.assertIn("未知引擎", " ".join(result["errors"]))

    def test_missing_dependency_is_reported(self):
        result = pdf.render("# 标题", self.out, engine="word")
        self.assertFalse(result["ok"])
        # Windows 上有 pywin32 时会走到真正的 Word 调用（可能失败），这里只要求有原因
        self.assertTrue(result.get("errors"))

    def test_successful_engine_writes_file(self):
        def fake_renderer(html: str, out_path: Path) -> None:
            out_path.write_bytes(b"%PDF-1.4 fake")

        pdf._RENDERERS["fake"] = (fake_renderer, "json")  # json 一定存在，骗过依赖检查
        self.addCleanup(lambda: pdf._RENDERERS.pop("fake", None))

        result = pdf.render("# 标题\n正文", self.out, engine="fake")
        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "fake")
        self.assertGreater(result["bytes"], 0)
        self.assertTrue(self.out.is_file())

    def test_engine_that_produces_nothing_fails(self):
        def lazy_renderer(html: str, out_path: Path) -> None:
            pass  # 什么都不写

        pdf._RENDERERS["lazy"] = (lazy_renderer, "json")
        self.addCleanup(lambda: pdf._RENDERERS.pop("lazy", None))

        result = pdf.render("# 标题", self.out, engine="lazy")
        self.assertFalse(result["ok"])
        self.assertIn("没有生成文件", " ".join(result["errors"]))

    def test_engine_exception_then_fallback(self):
        """第一个引擎炸了，应该继续试下一个，而不是直接失败。"""

        def broken(html: str, out_path: Path) -> None:
            raise RuntimeError("引擎崩了")

        def good(html: str, out_path: Path) -> None:
            out_path.write_bytes(b"%PDF-1.4 ok")

        pdf._RENDERERS["broken"] = (broken, "json")
        pdf._RENDERERS["good"] = (good, "json")
        self.addCleanup(lambda: pdf._RENDERERS.pop("broken", None))
        self.addCleanup(lambda: pdf._RENDERERS.pop("good", None))

        with mock.patch.object(pdf, "_resolve_engine", return_value=["broken", "good"]):
            result = pdf.render("# 标题", self.out)

        self.assertTrue(result["ok"])
        self.assertEqual(result["engine"], "good")


if __name__ == "__main__":
    unittest.main()
