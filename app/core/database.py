"""MySQL 数据库连接 - 连接池 + 瞬断重试"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator

import mysql.connector
from mysql.connector import Error, pooling

from app.core.config import get_settings

# MySQL 常见瞬断错误码：连接丢失 / 服务器已关闭连接
_TRANSIENT_ERRNOS = frozenset({2006, 2013, 2055})

_pool: pooling.MySQLConnectionPool | None = None


def get_connection_config() -> dict:
    s = get_settings()
    return {
        "host": s.mysql_host,
        "port": s.mysql_port,
        "user": s.mysql_user,
        "password": s.mysql_password,
        "database": s.mysql_database,
        "charset": "utf8mb4",
        "autocommit": False,
        "connection_timeout": 10,
    }


def _get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="mingyuan_erp",
            pool_size=10,
            pool_reset_session=True,
            **get_connection_config(),
        )
    return _pool


def _is_transient_error(exc: Error) -> bool:
    errno = getattr(exc, "errno", None)
    return errno in _TRANSIENT_ERRNOS


@contextmanager
def get_db() -> Generator:
    """获取数据库连接（连接池，瞬断自动重试一次）"""
    last_error: Error | None = None
    for attempt in range(2):
        conn = None
        try:
            conn = _get_pool().get_connection()
            yield conn
            conn.commit()
            return
        except Error as exc:
            last_error = exc
            if conn:
                try:
                    conn.rollback()
                except Error:
                    pass
            if attempt == 0 and _is_transient_error(exc):
                continue
            raise
        finally:
            if conn is not None:
                try:
                    if conn.is_connected():
                        conn.close()
                except Error:
                    pass
    if last_error is not None:
        raise last_error


def fetch_all(query: str, params: tuple[Any, ...] = ()) -> list[dict]:
    with get_db() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows


def fetch_one(query: str, params: tuple[Any, ...] = ()) -> dict | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def execute(query: str, params: tuple[Any, ...] = ()) -> int:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        lastrowid = cursor.lastrowid
        cursor.close()
        return int(lastrowid or 0)


def execute_many(query: str, params_list: list[tuple[Any, ...]]) -> None:
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.executemany(query, params_list)
        cursor.close()


def ping_database() -> bool:
    """健康检查：验证数据库可连通"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            cursor.close()
        return True
    except Error:
        return False
