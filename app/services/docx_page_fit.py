"""质保书 Word 单页排版：填模板后压缩至一页（Windows + MS Word）。"""

from __future__ import annotations

import sys
from pathlib import Path

from app.core.config import get_settings
from app.services.docx_to_pdf import _acquire_word_conversion_lock, _cleanup_orphan_word_processes

_WD_STATISTIC_PAGES = 2
_WD_REPLACE_ALL = 2
_WD_LINE_SPACE_SINGLE = 0
_MIN_FONT_PT = 6.0
_MAX_SHRINK_ROUNDS = 40


def _word_page_count(doc) -> int:
    return int(doc.ComputeStatistics(_WD_STATISTIC_PAGES))


def _remove_manual_page_breaks(word_app, doc) -> None:
    find = word_app.Selection.Find
    find.ClearFormatting()
    find.Replacement.ClearFormatting()
    find.Text = "^m"
    find.Replacement.Text = ""
    find.Forward = True
    find.Wrap = 1
    find.Execute(Replace=_WD_REPLACE_ALL)


def _shrink_document_one_step(doc) -> None:
    try:
        content = doc.Content
        size = float(content.Font.Size or 0)
        if size > _MIN_FONT_PT:
            content.Font.Size = max(size * 0.96, _MIN_FONT_PT)
    except Exception:
        pass

    try:
        for idx in range(1, doc.Paragraphs.Count + 1):
            fmt = doc.Paragraphs(idx).Format
            if fmt.SpaceBefore and fmt.SpaceBefore > 0:
                fmt.SpaceBefore = max(float(fmt.SpaceBefore) * 0.85, 0)
            if fmt.SpaceAfter and fmt.SpaceAfter > 0:
                fmt.SpaceAfter = max(float(fmt.SpaceAfter) * 0.85, 0)
            fmt.LineSpacingRule = _WD_LINE_SPACE_SINGLE
    except Exception:
        pass

    try:
        for idx in range(1, doc.Tables.Count + 1):
            table = doc.Tables(idx)
            size = float(table.Range.Font.Size or 0)
            if size > _MIN_FONT_PT:
                table.Range.Font.Size = max(size * 0.96, _MIN_FONT_PT)
    except Exception:
        pass


def _apply_print_fit_one_page(doc) -> None:
    setup = doc.PageSetup
    setup.FitToPagesWide = 1
    setup.FitToPagesTall = 1


def fit_docx_to_single_page(docx_path: Path) -> None:
    """将质保书 docx 压缩为单页；非 Windows 或未启用配置时跳过。"""
    if sys.platform != "win32":
        return
    if not get_settings().qc_cert_single_page:
        return

    docx_path = docx_path.resolve()
    if not docx_path.is_file():
        return

    timeout = float(get_settings().pdf_word_lock_timeout or 120)
    word_app = None
    doc = None

    with _acquire_word_conversion_lock(timeout):
        try:
            import win32com.client

            word_app = win32com.client.Dispatch("Word.Application")
            word_app.Visible = False
            word_app.DisplayAlerts = 0
            doc = word_app.Documents.Open(str(docx_path), ReadOnly=False)
            _remove_manual_page_breaks(word_app, doc)

            for _ in range(_MAX_SHRINK_ROUNDS):
                if _word_page_count(doc) <= 1:
                    break
                _shrink_document_one_step(doc)

            if _word_page_count(doc) > 1:
                _apply_print_fit_one_page(doc)

            doc.Save()
        except Exception:
            _cleanup_orphan_word_processes()
            raise
        finally:
            if doc is not None:
                doc.Close(SaveChanges=0)
            if word_app is not None:
                word_app.Quit()


def fit_qc_certificate_to_single_page(docx_path: Path) -> None:
    """质保书生成后调用，保证 Word/PDF 均为单页输出。"""
    fit_docx_to_single_page(docx_path)
