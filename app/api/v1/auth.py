"""认证相关 API - 登录等（预留）"""
from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["认证"])


@router.get("/me")
def get_current_user_info():
    """获取当前用户信息（后续接入 JWT + 用户表）"""
    return {"message": "请先实现登录与 JWT 校验"}
