"""
铭远ERP系统 API - 多语言版
FastAPI 后端入口
"""
from __future__ import annotations

import asyncio
import functools
import json
import os
import traceback
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.database import ping_database
from app.core.file_log import append_log_line
from app.core.paths import get_storage_dir
from app.api.v1 import auth, contract_archives, customer_packing, documents, drawing_archives, finance, heat_treatment, license, operations, price_split, resource_fetch, warehouse, workbench, workflow

settings = get_settings()
LOG_DIR = get_storage_dir() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


async def _run_in_thread(func, *args, **kwargs):
    """Python 3.8 无 asyncio.to_thread，用 run_in_executor 兼容。"""
    if hasattr(asyncio, "to_thread"):
        return await asyncio.to_thread(func, *args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


def _log_request_result(request: Request, status_code: int, detail: object | None = None) -> None:
    payload = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": request.method,
        "path": request.url.path,
        "query": str(request.url.query),
        "client": request.client.host if request.client else "",
        "status_code": status_code,
        "detail": detail,
    }
    append_log_line(LOG_DIR, f"api_{datetime.now().strftime('%Y-%m-%d')}.log", json.dumps(payload, ensure_ascii=False, default=str))


app = FastAPI(
    title=settings.app_name,
    description="铭远数字化管理系统(ERP) RESTful API，支持多语言(中/英/日)",
    version="0.1.0",
)

_cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://146.56.192.238",
    # "http://146.56.192.238:80",  # 与上一行等价（http 默认 80），且宿主机 80 不由本项目占用
    "null",
]
_extra_origins = os.getenv("CORS_ALLOW_ORIGINS", "")
if _extra_origins.strip():
    _cors_origins.extend(x.strip() for x in _extra_origins.split(",") if x.strip())

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_api_requests(request: Request, call_next):
    try:
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            await _run_in_thread(_log_request_result, request, response.status_code)
        return response
    except Exception as exc:
        await _run_in_thread(
            _log_request_result,
            request,
            500,
            {"error": str(exc), "traceback": traceback.format_exc()},
        )
        raise


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    await _run_in_thread(_log_request_result, request, 422, {"errors": errors})
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code >= 400:
        await _run_in_thread(_log_request_result, request, exc.status_code, {"detail": exc.detail})
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    await _run_in_thread(
        _log_request_result,
        request,
        500,
        {"error": str(exc), "traceback": traceback.format_exc()},
    )
    detail = str(exc) if settings.debug else "服务器内部错误，请稍后重试"
    return JSONResponse(status_code=500, content={"detail": detail})


app.include_router(finance.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(contract_archives.router, prefix="/api/v1")
app.include_router(customer_packing.router, prefix="/api/v1")
app.include_router(drawing_archives.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")
app.include_router(operations.router, prefix="/api/v1")
app.include_router(workflow.router, prefix="/api/v1")
app.include_router(price_split.router, prefix="/api/v1")
app.include_router(heat_treatment.router, prefix="/api/v1")
app.include_router(license.router, prefix="/api/v1")
app.include_router(resource_fetch.router, prefix="/api/v1")
app.include_router(warehouse.router, prefix="/api/v1")
app.include_router(workbench.router, prefix="/api/v1")


@app.get("/")
def root():
    return {"app": settings.app_name, "version": "0.1.0", "docs": "/docs"}


@app.get("/health")
def health():
    db_ok = ping_database()
    return {"status": "ok" if db_ok else "degraded", "database": "ok" if db_ok else "unavailable"}
