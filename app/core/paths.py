"""统一解析后端目录、项目根目录、templates 与 storage / data 路径（兼容本地 monorepo 与服务器部署）。"""
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
def get_data_dir() -> Path:
    """
    业务持久数据根目录（发版时不应覆盖）。
    优先 ERP_DATA_DIR / Settings.erp_data_dir；否则使用项目旁 data/。
    """
    from app.core.config import get_settings

    configured = (get_settings().erp_data_dir or os.getenv("ERP_DATA_DIR", "")).strip()
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        # 开发默认：与 backend 同级的 data（不在代码包内被误打包时仍建议生产显式配置）
        path = (get_project_root() / "data").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_contract_archives_dir() -> Path:
    path = get_data_dir() / "contract_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_drawing_archives_dir() -> Path:
    path = get_data_dir() / "drawing_archives"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def get_storage_dir() -> Path:
    """运行时生成物/日志目录。若已配置 ERP_DATA_DIR，优先使用 data 下子目录以与代码解耦。"""
    data_dir = get_data_dir()
    preferred_under_data = data_dir / "runtime"
    # 若 ERP_DATA_DIR 已显式配置，或 data/runtime 已存在，则使用数据盘
    configured = (os.getenv("ERP_DATA_DIR", "") or "").strip()
    if configured or preferred_under_data.is_dir():
        preferred_under_data.mkdir(parents=True, exist_ok=True)
        (preferred_under_data / "logs").mkdir(parents=True, exist_ok=True)
        return preferred_under_data

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
