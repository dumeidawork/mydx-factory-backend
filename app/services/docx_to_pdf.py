"""docx → pdf：服务器端 Word 或 LibreOffice 转换（客户端无需安装转换组件）。"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from fastapi import HTTPException

from app.core.config import get_settings

_word_thread_lock = threading.Lock()
_WORD_LOCK_FILE = Path(tempfile.gettempdir()) / "mingyuan_word_pdf.lock"


def resolve_libreoffice_binary() -> Path | None:
    settings = get_settings()
    custom = (settings.libreoffice_path or "").strip()
    if custom:
        path = Path(custom).expanduser()
        if path.is_file():
            return path
    for candidate in (
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files\LibreOffice 7\program\soffice.exe"),
        Path("/usr/bin/libreoffice"),
        Path("/usr/bin/soffice"),
    ):
        if candidate.is_file():
            return candidate
    found = shutil.which("soffice") or shutil.which("soffice.exe") or shutil.which("libreoffice")
    return Path(found) if found else None


def _subprocess_kwargs() -> dict:
    kwargs: dict = {"capture_output": True, "text": True, "timeout": 180, "check": False}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


@contextlib.contextmanager
def _acquire_word_conversion_lock(timeout_seconds: float):
    """进程内 + 跨进程串行化 Word COM，避免并发启动多个 WINWORD。"""
    acquired_file = False
    lock_handle = None
    deadline = time.monotonic() + max(timeout_seconds, 1.0)

    with _word_thread_lock:
        if sys.platform == "win32":
            import msvcrt

            _WORD_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            lock_handle = open(_WORD_LOCK_FILE, "a+b")
            while True:
                try:
                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired_file = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        lock_handle.close()
                        raise TimeoutError(
                            f"等待 Word PDF 转换锁超时（{int(timeout_seconds)}s）；"
                            "可能有未结束的 WINWORD 进程，请检查任务管理器后重试"
                        ) from None
                    time.sleep(0.25)
        try:
            yield
        finally:
            if sys.platform == "win32" and lock_handle is not None:
                if acquired_file:
                    import msvcrt

                    try:
                        lock_handle.seek(0)
                        msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                lock_handle.close()


def _cleanup_orphan_word_processes() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/F", "/IM", "WINWORD.EXE"],
        **_subprocess_kwargs(),
    )


def _convert_with_word(docx_path: Path, pdf_path: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("PDF_CONVERTER=word 仅支持 Windows 服务器（需安装 Microsoft Word）")

    docx_path = docx_path.resolve()
    pdf_path = pdf_path.resolve()
    if not docx_path.is_file():
        raise RuntimeError(f"docx 文件不存在: {docx_path}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()

    settings = get_settings()
    timeout = float(settings.pdf_word_lock_timeout or 120)

    with _acquire_word_conversion_lock(timeout):
        try:
            from docx2pdf import convert

            convert(str(docx_path), str(pdf_path))
        except TimeoutError:
            raise
        except Exception as exc:
            _cleanup_orphan_word_processes()
            raise RuntimeError(str(exc)) from exc

    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        _cleanup_orphan_word_processes()
        raise RuntimeError("Word 未生成 PDF 文件")


def _convert_with_libreoffice(docx_path: Path, pdf_path: Path) -> None:
    soffice = resolve_libreoffice_binary()
    if not soffice:
        raise RuntimeError(
            "未找到 LibreOffice。请在服务器安装 LibreOffice，"
            "并在 .env 设置 LIBREOFFICE_PATH（例如 C:/Program Files/LibreOffice/program/soffice.exe）"
        )

    docx_path = docx_path.resolve()
    pdf_path = pdf_path.resolve()
    if not docx_path.is_file():
        raise RuntimeError(f"docx 文件不存在: {docx_path}")

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = pdf_path.parent
    profile_dir = Path(tempfile.mkdtemp(prefix="mingyuan_lo_profile_"))
    user_install_uri = profile_dir.resolve().as_uri()

    cmd = [
        str(soffice),
        "--headless",
        "--invisible",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        f"-env:UserInstallation={user_install_uri}",
        "--convert-to",
        "pdf:writer_pdf_Export",
        "--outdir",
        str(out_dir),
        str(docx_path),
    ]

    last_error = ""
    for attempt in range(2):
        completed = subprocess.run(cmd, **_subprocess_kwargs())
        if completed.returncode == 0:
            generated = out_dir / f"{docx_path.stem}.pdf"
            if generated.is_file():
                if generated.resolve() != pdf_path.resolve():
                    if pdf_path.exists():
                        pdf_path.unlink()
                    shutil.move(str(generated), str(pdf_path))
                if pdf_path.is_file():
                    shutil.rmtree(profile_dir, ignore_errors=True)
                    return
            last_error = "LibreOffice 未生成 PDF 文件"
        else:
            stderr = (completed.stderr or completed.stdout or "").strip()
            last_error = stderr or f"LibreOffice 退出码 {completed.returncode}"
        if attempt == 0:
            time.sleep(1.5)

    shutil.rmtree(profile_dir, ignore_errors=True)
    raise RuntimeError(f"{last_error}（soffice: {soffice}）")


def convert_docx_to_pdf(docx_path: Path, pdf_path: Path) -> None:
    """将 docx 转为 pdf；由 PDF_CONVERTER 选择 Word 或 LibreOffice。"""
    mode = (get_settings().pdf_converter or "word").strip().lower()

    if mode == "word":
        try:
            _convert_with_word(docx_path, pdf_path)
        except HTTPException:
            raise
        except TimeoutError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"PDF 转换失败（需本机安装 MS Word）: {exc}",
            ) from exc
        return

    if mode == "libreoffice":
        try:
            _convert_with_libreoffice(docx_path, pdf_path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"PDF 转换失败（LibreOffice）：{exc}") from exc
        return

    if mode == "auto":
        if sys.platform == "win32":
            try:
                _convert_with_word(docx_path, pdf_path)
                return
            except (TimeoutError, HTTPException):
                raise
            except Exception:
                pass
        try:
            _convert_with_libreoffice(docx_path, pdf_path)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"PDF 转换失败（Word 与 LibreOffice 均不可用）：{exc}",
            ) from exc
        return

    raise HTTPException(
        status_code=500,
        detail=f"不支持的 PDF_CONVERTER={mode!r}，请设为 word、libreoffice 或 auto",
    )
