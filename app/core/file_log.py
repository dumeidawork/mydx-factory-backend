"""线程安全的追加写日志，避免整文件读写阻塞与并发覆盖。"""
from __future__ import annotations

import threading
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def append_log_line(log_dir: Path, file_name: str, line: str) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / file_name
    text = line if line.endswith("\n") else f"{line}\n"
    with _lock_for(log_file):
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(text)
