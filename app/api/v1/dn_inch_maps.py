"""DN 值与英寸对照：独立表与操作日志。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one

router = APIRouter(prefix="/dn-inch-maps", tags=["DN值-英寸"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS dn_inch_maps (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            dn_value VARCHAR(32) NOT NULL COMMENT 'DN值，如 DN15',
            inch_value VARCHAR(32) NOT NULL COMMENT '英寸，如 1/2"',
            remark TEXT NULL COMMENT '备注说明',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_dn_inch_dn (dn_value),
            INDEX idx_dn_inch_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DN值-英寸对照'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS dn_inch_map_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '对照表ID，删除后可空',
            dn_value VARCHAR(32) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_dn_inch_log_row (row_id),
            INDEX idx_dn_inch_log_dn (dn_value),
            INDEX idx_dn_inch_log_action (action),
            INDEX idx_dn_inch_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='DN值-英寸对照操作日志'
    """

SELECT_SQL = """
        id, dn_value, inch_value, remark,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = ("dn_value", "inch_value", "remark")
_DN_NUM = re.compile(r"^DN(\d+)$", re.I)


class DnInchRowIn(BaseModel):
    id: int | None = None
    dn_value: str = ""
    inch_value: str = ""
    remark: str = ""


class DnInchSaveRequest(BaseModel):
    rows: list[DnInchRowIn] = Field(default_factory=list)


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_TABLE_SQL)
    execute(CREATE_LOG_TABLE_SQL)
    _TABLES_READY = True


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_dn_value(raw: str) -> str:
    text = _normalize_str(raw).upper().replace(" ", "")
    if not text:
        return ""
    if text.startswith("DN"):
        rest = text[2:]
        if rest.isdigit():
            return f"DN{int(rest)}"
        return f"DN{rest}"
    if text.isdigit():
        return f"DN{int(text)}"
    return text


def normalize_inch_value(raw: str) -> str:
    text = _normalize_str(raw)
    if not text:
        return ""
    for ch in ("”", "＂", "″", "''", "“"):
        text = text.replace(ch, '"')
    text = text.replace("＇", "'").strip()
    if text and '"' not in text and re.search(r"[\d/]", text):
        text = f'{text}"'
    return text


def _dn_sort_key(dn_value: str) -> tuple[int, str]:
    match = _DN_NUM.match(dn_value or "")
    if match:
        return (int(match.group(1)), dn_value)
    return (10**9, dn_value or "")


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "dn_value": _normalize_str(row.get("dn_value")),
        "inch_value": _normalize_str(row.get("inch_value")),
        "remark": _normalize_str(row.get("remark")),
        "created_by": _normalize_str(row.get("created_by")),
        "created_by_user_id": row.get("created_by_user_id"),
        "created_at": _jsonable(row.get("created_at")) or "",
        "updated_by": _normalize_str(row.get("updated_by")),
        "updated_by_user_id": row.get("updated_by_user_id"),
        "updated_at": _jsonable(row.get("updated_at")) or "",
    }


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "dn_value": row.get("dn_value") or "",
        "inch_value": row.get("inch_value") or "",
        "remark": row.get("remark") or "",
    }


def _diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in DIFF_KEYS:
        left = _normalize_str(before.get(key))
        right = _normalize_str(after.get(key))
        if left != right:
            out[key] = {"before": left, "after": right}
    return out


def _write_log(
    *,
    action: str,
    user: CurrentUser,
    row_id: int | None,
    dn_value: str,
    change_summary: dict[str, Any],
) -> None:
    execute(
        """
        INSERT INTO dn_inch_map_logs
            (row_id, dn_value, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            dn_value,
            action,
            user.id,
            user.name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_by_dn(dn_value: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE dn_value=%s", (dn_value,))
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE dn_value=%s AND id<>%s",
        (dn_value, exclude_id),
    )


def load_inch_map() -> dict[str, str]:
    """打字要求生成用：DN 值（大写）→ 英寸。"""
    _ensure_tables()
    rows = fetch_all("SELECT dn_value, inch_value FROM dn_inch_maps")
    out: dict[str, str] = {}
    for row in rows:
        key = _normalize_str(row.get("dn_value")).upper()
        value = _normalize_str(row.get("inch_value"))
        if key and value:
            out[key] = value
    return out


@router.get("")
def list_dn_inch_maps(_user: UserDep, dn_value: str = "", limit: int = Query(2000, ge=1, le=5000)):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = normalize_dn_value(dn_value) or _normalize_str(dn_value)
    if query:
        clauses.append("(dn_value LIKE %s OR inch_value LIKE %s)")
        like = f"%{query}%"
        params.extend([like, f"%{_normalize_str(dn_value)}%"])
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM dn_inch_maps
        WHERE {' AND '.join(clauses)}
        ORDER BY id
        LIMIT %s
        """,
        (*params, limit),
    )
    data = [_row_from_db(r) for r in rows]
    data.sort(key=lambda item: _dn_sort_key(str(item.get("dn_value") or "")))
    return {"status": "success", "total": len(data), "rows": data}


@router.get("/logs")
def list_dn_inch_map_logs(
    _user: UserDep,
    dn_value: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = normalize_dn_value(dn_value) or _normalize_str(dn_value)
    if query:
        clauses.append("dn_value LIKE %s")
        params.append(f"%{query}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, dn_value, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM dn_inch_map_logs
        WHERE {' AND '.join(clauses)}
        ORDER BY id DESC
        LIMIT %s
        """,
        (*params, limit),
    )
    data = []
    for row in rows:
        item = {k: _jsonable(v) for k, v in row.items()}
        item["operator_name"] = _normalize_str(item.get("operator_name"))
        item["change_summary"] = item.get("change_summary") or ""
        data.append(item)
    return {"status": "success", "total": len(data), "rows": data}


@router.post("/save")
def save_dn_inch_maps(req: DnInchSaveRequest, user: UserDep):
    _ensure_tables()
    prepared: list[tuple[int | None, str, str, str]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(req.rows, start=1):
        dn_value = normalize_dn_value(raw.dn_value)
        inch_value = normalize_inch_value(raw.inch_value)
        remark = _normalize_str(raw.remark)
        if not dn_value and not inch_value and not remark:
            continue
        if not dn_value:
            raise HTTPException(status_code=400, detail=f"第{idx}行 DN 值不能为空")
        if not inch_value:
            raise HTTPException(status_code=400, detail=f"第{idx}行英寸不能为空")
        if dn_value in seen:
            raise HTTPException(status_code=400, detail=f"本次保存中 DN 值重复：{dn_value}")
        seen.add(dn_value)
        row_id = int(raw.id) if raw.id else None
        prepared.append((row_id, dn_value, inch_value, remark))

    if not prepared:
        raise HTTPException(status_code=400, detail="没有可保存的行，请至少填写 DN 值与英寸")

    saved: list[dict[str, Any]] = []
    inserted = 0
    updated = 0
    for row_id, dn_value, inch_value, remark in prepared:
        if row_id:
            current = fetch_one(f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE id=%s", (row_id,))
            if not current:
                raise HTTPException(status_code=404, detail=f"未找到对照记录 id={row_id}")
            dup = _find_by_dn(dn_value, exclude_id=row_id)
            if dup:
                raise HTTPException(status_code=400, detail=f"该 DN 值已存在对照：{dn_value}")
            before = _row_from_db(current)
            after_snap = {"dn_value": dn_value, "inch_value": inch_value, "remark": remark}
            fields = _diff_fields(_snapshot(before), after_snap)
            if not fields:
                saved.append(before)
                continue
            execute(
                """
                UPDATE dn_inch_maps
                SET dn_value=%s, inch_value=%s, remark=%s,
                    updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (dn_value, inch_value, remark, user.name, user.id, row_id),
            )
            after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE id=%s", (row_id,)))
            _write_log(
                action="update",
                user=user,
                row_id=row_id,
                dn_value=dn_value,
                change_summary={"op": "update", "fields": fields},
            )
            updated += 1
            saved.append(after)
            continue
        if _find_by_dn(dn_value):
            raise HTTPException(status_code=400, detail=f"该 DN 值已存在对照：{dn_value}")
        new_id = execute(
            """
            INSERT INTO dn_inch_maps (
                dn_value, inch_value, remark,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (dn_value, inch_value, remark, user.name, user.id, user.name, user.id),
        )
        after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE id=%s", (new_id,)))
        _write_log(
            action="create",
            user=user,
            row_id=new_id,
            dn_value=dn_value,
            change_summary={"op": "create", "fields": _snapshot(after)},
        )
        inserted += 1
        saved.append(after)

    saved.sort(key=lambda item: _dn_sort_key(str(item.get("dn_value") or "")))
    return {
        "status": "success",
        "inserted_count": inserted,
        "updated_count": updated,
        "saved_count": len(saved),
        "rows": saved,
    }


@router.delete("/{row_id}")
def delete_dn_inch_map(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM dn_inch_maps WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到对照记录")
    before = _row_from_db(current)
    execute("DELETE FROM dn_inch_maps WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        dn_value=before["dn_value"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}
