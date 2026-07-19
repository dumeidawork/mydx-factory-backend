"""后端多语言错误/提示信息 - 根据 Accept-Language 返回"""
from typing import Dict

# 业务错误信息多语言字典（可后续扩展）
ERROR_MESSAGES: Dict[str, Dict[str, str]] = {
    "zh-CN": {
        "auth_failed": "权限不足，无法审批此付款单。",
        "not_found": "资源不存在。",
        "invalid_request": "请求参数无效。",
        "export_failed": "导出失败。",
    },
    "en-US": {
        "auth_failed": "Permission denied. Cannot approve this payment.",
        "not_found": "Resource not found.",
        "invalid_request": "Invalid request parameters.",
        "export_failed": "Export failed.",
    },
    "ja-JP": {
        "auth_failed": "権限がありません。この支払いを承認できません。",
        "not_found": "リソースが見つかりません。",
        "invalid_request": "リクエストパラメータが無効です。",
        "export_failed": "エクスポートに失敗しました。",
    },
}

DEFAULT_LANG = "zh-CN"
SUPPORTED_LANGS = ("zh-CN", "en-US", "ja-JP")


def get_message(lang: str, key: str) -> str:
    """根据语言和 key 获取文案，不存在则回退到默认语言"""
    if lang not in SUPPORTED_LANGS:
        lang = DEFAULT_LANG
    return ERROR_MESSAGES.get(lang, ERROR_MESSAGES[DEFAULT_LANG]).get(
        key, ERROR_MESSAGES[DEFAULT_LANG].get(key, key)
    )
