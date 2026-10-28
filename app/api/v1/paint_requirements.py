"""划漆要求：图纸号-版本号 + 是否不划绿漆 + 备注，独立表与操作日志。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.utils.drawing_no import drawing_no_match_variants, normalize_drawing_rev_for_db

router = APIRouter(prefix="/paint-requirements", tags=["划漆要求"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS paint_requirements (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            drawing_rev_no VARCHAR(255) NOT NULL COMMENT '图纸号-版本号（Rev 形）',
            skip_green_paint TINYINT NOT NULL DEFAULT 0 COMMENT '1=不划绿漆 0=划绿漆',
            remark TEXT NULL COMMENT '备注说明',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_paint_drawing_rev (drawing_rev_no),
            INDEX idx_paint_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='划漆要求'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS paint_requirement_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '划漆要求ID，删除后可空',
            drawing_rev_no VARCHAR(255) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_paint_log_row (row_id),
            INDEX idx_paint_log_drawing (drawing_rev_no),
            INDEX idx_paint_log_action (action),
            INDEX idx_paint_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='划漆要求操作日志'
    """

SELECT_SQL = """
        id, drawing_rev_no, skip_green_paint, remark,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = ("drawing_rev_no", "skip_green_paint", "remark")


class PaintCreateRequest(BaseModel):
    drawing_rev_no: str = Field(..., min_length=1)
    skip_green_paint: bool = False
    remark: str = ""


class PaintUpdateRequest(BaseModel):
    drawing_rev_no: str | None = None
    skip_green_paint: bool | None = None
    remark: str | None = None


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


def _normalize_rev(raw: str) -> str:
    text = _normalize_str(raw)
    if not text:
        return ""
    return normalize_drawing_rev_for_db(text)


def _as_flag(value: Any) -> int:
    if value in (True, 1, "1", "true", "True"):
        return 1
    return 0


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "drawing_rev_no": _normalize_str(row.get("drawing_rev_no")),
        "skip_green_paint": _as_flag(row.get("skip_green_paint")),
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
        "drawing_rev_no": row.get("drawing_rev_no") or "",
        "skip_green_paint": int(row.get("skip_green_paint") or 0),
        "remark": row.get("remark") or "",
    }


def _diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in DIFF_KEYS:
        left = before.get(key)
        right = after.get(key)
        if key == "skip_green_paint":
            if int(left or 0) != int(right or 0):
                out[key] = {"before": int(left or 0), "after": int(right or 0)}
            continue
        if _normalize_str(left) != _normalize_str(right):
            out[key] = {"before": left, "after": right}
    return out


def _write_log(
    *,
    action: str,
    user: CurrentUser,
    row_id: int | None,
    drawing_rev_no: str,
    change_summary: dict[str, Any],
) -> None:
    execute(
        """
        INSERT INTO paint_requirement_logs
            (row_id, drawing_rev_no, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            drawing_rev_no,
            action,
            user.id,
            user.name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _like_variants(raw: str) -> list[str]:
    text = _normalize_str(raw)
    if not text:
        return []
    variants = drawing_no_match_variants(text)
    if text not in variants:
        variants.insert(0, text)
    return variants


def _find_by_rev(drawing_rev_no: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(
            f"SELECT {SELECT_SQL} FROM paint_requirements WHERE drawing_rev_no=%s",
            (drawing_rev_no,),
        )
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM paint_requirements WHERE drawing_rev_no=%s AND id<>%s",
        (drawing_rev_no, exclude_id),
    )


@router.get("")
def list_paint_requirements(_user: UserDep, drawing_rev_no: str = "", limit: int = Query(500, ge=1, le=2000)):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    variants = _like_variants(drawing_rev_no)
    if variants:
        clauses.append("(" + " OR ".join(["drawing_rev_no LIKE %s"] * len(variants)) + ")")
        params.extend([f"%{item}%" for item in variants])
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM paint_requirements
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        (*params, limit),
    )
    return {"status": "success", "total": len(rows), "rows": [_row_from_db(r) for r in rows]}


@router.get("/logs")
def list_paint_requirement_logs(
    _user: UserDep,
    drawing_rev_no: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    variants = _like_variants(drawing_rev_no)
    if variants:
        clauses.append("(" + " OR ".join(["drawing_rev_no LIKE %s"] * len(variants)) + ")")
        params.extend([f"%{item}%" for item in variants])
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, drawing_rev_no, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM paint_requirement_logs
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


@router.post("")
def create_paint_requirement(req: PaintCreateRequest, user: UserDep):
    _ensure_tables()
    drawing_rev_no = _normalize_rev(req.drawing_rev_no)
    if not drawing_rev_no:
        raise HTTPException(status_code=400, detail="图纸号-版本号不能为空")
    if _find_by_rev(drawing_rev_no):
        raise HTTPException(status_code=400, detail=f"该图纸号-版本号已存在划漆要求：{drawing_rev_no}")
    skip_flag = 1 if req.skip_green_paint else 0
    remark = _normalize_str(req.remark)
    new_id = execute(
        """
        INSERT INTO paint_requirements (
            drawing_rev_no, skip_green_paint, remark,
            created_by, created_by_user_id, updated_by, updated_by_user_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (drawing_rev_no, skip_flag, remark, user.name, user.id, user.name, user.id),
    )
    saved = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM paint_requirements WHERE id=%s", (new_id,)))
    _write_log(
        action="create",
        user=user,
        row_id=new_id,
        drawing_rev_no=drawing_rev_no,
        change_summary={"op": "create", "fields": _snapshot(saved)},
    )
    return {"status": "success", "row": saved}


@router.patch("/{row_id}")
def update_paint_requirement(row_id: int, req: PaintUpdateRequest, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM paint_requirements WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到划漆要求")
    before = _row_from_db(current)
    drawing_rev_no = before["drawing_rev_no"]
    if req.drawing_rev_no is not None:
        drawing_rev_no = _normalize_rev(req.drawing_rev_no)
        if not drawing_rev_no:
            raise HTTPException(status_code=400, detail="图纸号-版本号不能为空")
        dup = _find_by_rev(drawing_rev_no, exclude_id=row_id)
        if dup:
            raise HTTPException(status_code=400, detail=f"该图纸号-版本号已存在划漆要求：{drawing_rev_no}")
    skip_flag = before["skip_green_paint"] if req.skip_green_paint is None else (1 if req.skip_green_paint else 0)
    remark = before["remark"] if req.remark is None else _normalize_str(req.remark)
    execute(
        """
        UPDATE paint_requirements
        SET drawing_rev_no=%s, skip_green_paint=%s, remark=%s,
            updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (drawing_rev_no, skip_flag, remark, user.name, user.id, row_id),
    )
    after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM paint_requirements WHERE id=%s", (row_id,)))
    fields = _diff_fields(_snapshot(before), _snapshot(after))
    if fields:
        _write_log(
            action="update",
            user=user,
            row_id=row_id,
            drawing_rev_no=after["drawing_rev_no"],
            change_summary={"op": "update", "fields": fields},
        )
    return {"status": "success", "row": after}


@router.delete("/{row_id}")
def delete_paint_requirement(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM paint_requirements WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到划漆要求")
    before = _row_from_db(current)
    execute("DELETE FROM paint_requirements WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        drawing_rev_no=before["drawing_rev_no"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}
