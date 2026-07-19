"""API 依赖：语言头、数据库等"""
from fastapi import Header
from app.core.i18n import DEFAULT_LANG, SUPPORTED_LANGS


def get_accept_language(accept_language: str = Header(default="zh-CN")) -> str:
    """从请求头解析客户端语言偏好"""
    if accept_language in SUPPORTED_LANGS:
        return accept_language
    # 简单处理 en-US, en 等
    for lang in SUPPORTED_LANGS:
        if lang.startswith(accept_language.split("-")[0]):
            return lang
    return DEFAULT_LANG
