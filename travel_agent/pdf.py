"""Markdown → PDF，在后端渲染。

设计要点
--------
1. **Markdown 不能直接变 PDF**。它没有排版信息，必须经过 HTML（或排版引擎）。
   所以链路固定是 `MD → HTML → PDF`，三套引擎共用同一个 HTML。
2. **引擎可插拔**，因为它强依赖运行环境：

   | 引擎 | 平台 | 额外要求 | 适用 |
   |---|---|---|---|
   | `weasyprint` | 跨平台（Linux 首选） | 系统库 pango/cairo/gdk-pixbuf + 中文字体 | **服务器部署推荐** |
   | `xhtml2pdf` | 跨平台，纯 wheel | 无系统依赖；中文要注册 TTF | 装不上系统库时的备选 |
   | `word` | 仅 Windows | 已安装 Microsoft Word | 本地开发（复用 Word 排版） |

3. **失败要说人话**。没装引擎时返回安装命令，而不是抛一个 ImportError 让人猜。

引擎选择：`PDF_ENGINE=auto|weasyprint|xhtml2pdf|word`，`auto` 按上表顺序挑第一个可用的。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

#: Windows + Word 的 COM 不是线程安全的，并发转换必须串行
_WORD_LOCK = threading.Lock()

INSTALL_HINT = (
    "没有可用的 PDF 引擎。任选一种安装：\n"
    '  · 跨平台推荐：pip install weasyprint  '
    "（Linux 还需 apt install libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 fonts-noto-cjk）\n"
    '  · 纯 wheel：  pip install xhtml2pdf\n'
    '  · Windows：   pip install pywin32  （需已安装 Microsoft Word）\n'
    "另外两个都需要：pip install markdown"
)

#: A4 + 中文字体栈。CJK 字体没配好，PDF 里中文会变方块。
HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 16mm 15mm; }}
  body {{ font-family: "Noto Sans CJK SC", "Source Han Sans SC", "Microsoft YaHei",
         "PingFang SC", "WenQuanYi Micro Hei", sans-serif;
         font-size: 10.5pt; line-height: 1.7; color: #111; }}
  h1 {{ font-size: 19pt; margin: 0 0 10px; }}
  h2 {{ font-size: 14pt; margin: 18px 0 8px; padding-bottom: 3px;
        border-bottom: 1px solid #ddd; page-break-after: avoid; }}
  h3 {{ font-size: 11.5pt; margin: 12px 0 6px; page-break-after: avoid; }}
  table {{ border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 9.5pt; }}
  th, td {{ border: 1px solid #999; padding: 4px 7px; text-align: left; vertical-align: top; }}
  th {{ background: #f0f0f0; }}
  code {{ font-family: Consolas, "Courier New", monospace; background: #f4f4f4;
          padding: 0 3px; font-size: 9.5pt; }}
  pre {{ background: #f7f7f7; padding: 10px; border-radius: 4px; overflow-x: auto; }}
  blockquote {{ margin: 8px 0; padding: 4px 12px; border-left: 3px solid #ccc; color: #444; }}
  a {{ color: #0b57d0; word-break: break-all; }}
  ul, ol {{ margin: 6px 0 6px 20px; }}
  li {{ margin: 2px 0; }}
  table, pre, blockquote {{ page-break-inside: avoid; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# MD → HTML
# --------------------------------------------------------------------------- #
def build_html(markdown_text: str, title: str = "旅游策划书") -> str:
    """把 markdown 转成带打印样式的完整 HTML。"""
    try:
        import markdown
    except ImportError as exc:  # pragma: no cover - 环境问题，提示清楚即可
        raise RuntimeError("缺少 markdown 库，请先：pip install markdown") from exc

    body = markdown.markdown(
        markdown_text or "",
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    return HTML_TEMPLATE.format(title=title, body=body)


# --------------------------------------------------------------------------- #
# 三个渲染后端
# --------------------------------------------------------------------------- #
def _render_weasyprint(html: str, out_path: Path) -> None:
    from weasyprint import HTML  # type: ignore

    HTML(string=html).write_pdf(str(out_path))


def _render_xhtml2pdf(html: str, out_path: Path) -> None:
    from xhtml2pdf import pisa  # type: ignore

    with out_path.open("wb") as fh:
        status = pisa.CreatePDF(html, dest=fh, encoding="utf-8")
    if status.err:
        raise RuntimeError(f"xhtml2pdf 报告 {status.err} 个错误")


def _render_word(html: str, out_path: Path) -> None:
    """用本机 Microsoft Word 的 COM 接口转 PDF（仅 Windows）。

    注意几个坑：
    - COM 必须在**调用它的那个线程**里 CoInitialize / CoUninitialize；
    - Word 不并发，多请求同时转会互相抢，所以加锁串行；
    - 打开本地 HTML 时 Word 可能弹"受保护的视图"，最好在 Word 里把该目录加入信任位置。
    """
    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    temp_html = out_path.with_suffix(".temp.html")
    temp_html.write_text(html, encoding="utf-8")

    with _WORD_LOCK:
        pythoncom.CoInitialize()
        word = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            word.DisplayAlerts = False

            doc = word.Documents.Open(str(temp_html.resolve()))
            try:
                doc.SaveAs(str(out_path.resolve()), FileFormat=17)  # 17 = wdFormatPDF
            finally:
                doc.Close(SaveChanges=0)
        finally:
            if word is not None:
                try:
                    word.Quit()
                except Exception:  # noqa: BLE001 - 退出清理失败不该掩盖真正的错误
                    log.warning("Word 退出失败", exc_info=True)
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
            temp_html.unlink(missing_ok=True)


_RENDERERS = {
    "weasyprint": (_render_weasyprint, "weasyprint"),
    "xhtml2pdf": (_render_xhtml2pdf, "xhtml2pdf"),
    "word": (_render_word, "win32com.client"),
}

#: auto 模式的尝试顺序：跨平台的优先，Windows 的兜底
_AUTO_ORDER = ["weasyprint", "xhtml2pdf", "word"]


# --------------------------------------------------------------------------- #
# 引擎探测与渲染
# --------------------------------------------------------------------------- #
def _has_module(name: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def available_engines() -> list[str]:
    """当前环境真正能用的引擎（只检查依赖是否装了，不做实际渲染）。"""
    engines = []
    for name, (_fn, module) in _RENDERERS.items():
        if _has_module(module):
            engines.append(name)
    return [name for name in _AUTO_ORDER if name in engines]


def _resolve_engine(engine: str) -> list[str]:
    if engine == "auto":
        forced = os.getenv("PDF_ENGINE", "auto").strip().lower()
        if forced and forced != "auto":
            engine = forced
    if engine == "auto":
        return available_engines()
    return [engine]


def render(markdown_text: str, out_path: Path, engine: str = "auto") -> dict:
    """渲染 PDF。返回 {"ok": True, "engine": ..., "file": ...} 或 {"ok": False, ...}。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = _resolve_engine(engine)
    if not candidates:
        return {"ok": False, "engine": None, "error": "没有可用的 PDF 引擎", "hint": INSTALL_HINT}

    try:
        html = build_html(markdown_text)
    except RuntimeError as exc:
        return {"ok": False, "engine": None, "error": str(exc), "hint": INSTALL_HINT}

    errors: list[str] = []
    for name in candidates:
        renderer = _RENDERERS.get(name)
        if renderer is None:
            errors.append(f"{name}: 未知引擎")
            continue
        fn, module = renderer
        if not _has_module(module):
            errors.append(f"{name}: 缺少依赖 {module}")
            continue
        try:
            fn(html, out_path)
        except Exception as exc:  # noqa: BLE001 - 引擎千奇百怪，统一兜住
            log.warning("PDF 引擎 %s 失败", name, exc_info=True)
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        if out_path.is_file() and out_path.stat().st_size > 0:
            return {
                "ok": True,
                "engine": name,
                "file": out_path.name,
                "bytes": out_path.stat().st_size,
            }
        errors.append(f"{name}: 没有生成文件")

    return {
        "ok": False,
        "engine": None,
        "error": "所有 PDF 引擎都失败了",
        "errors": errors,
        "hint": INSTALL_HINT,
    }
