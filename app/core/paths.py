"""统一解析后端目录、项目根目录、templates 与 storage 路径（兼容本地 monorepo 与服务器部署）。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def get_backend_dir() -> Path:
    return _BACKEND_DIR


@lru_cache(maxsize=1)
def get_project_root() -> Path:
    env_root = os.getenv("ERP_PROJECT_ROOT", "").strip()
    if env_root:
        path = Path(env_root).resolve()
        if path.is_dir():
            return path

    bundled_templates = _BACKEND_DIR / "templates"
    if bundled_templates.is_dir():
        return _BACKEND_DIR.parent

    for candidate in (_BACKEND_DIR.parent, *_BACKEND_DIR.parents):
        if (candidate / "templates").is_dir():
            return candidate
        if candidate.name == "backend" and (candidate.parent / "templates").is_dir():
            return candidate.parent

    return _BACKEND_DIR.parent


@lru_cache(maxsize=1)
def get_templates_dir() -> Path:
    root = get_project_root()
    bundled = _BACKEND_DIR / "templates"
    if bundled.is_dir():
        return bundled
    if (root / "templates").is_dir():
        return root / "templates"
    return root / "templates"


@lru_cache(maxsize=1)
def get_storage_dir() -> Path:
    for candidate in (
        _BACKEND_DIR / "storage",
        get_project_root() / "storage",
        _BACKEND_DIR.parent / "storage",
    ):
        if candidate.is_dir():
            return candidate
    preferred = _BACKEND_DIR / "storage"
    preferred.mkdir(parents=True, exist_ok=True)
    return preferred
