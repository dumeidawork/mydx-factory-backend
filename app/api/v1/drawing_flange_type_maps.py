"""图纸号与法兰类型对照：独立表与操作日志。"""
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
from app.utils.drawing_no import drawing_no_base

router = APIRouter(prefix="/drawing-flange-type-maps", tags=["图纸-法兰类型"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

FLANGE_TYPES = ("滑动", "平板", "盲板", "高径")
DEFAULT_FLANGE_TYPE = "平板"
SEED_OPERATOR = "系统预置"

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS drawing_flange_type_maps (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            drawing_no VARCHAR(128) NOT NULL COMMENT '图纸号（不含版本号）',
            flange_type VARCHAR(16) NOT NULL COMMENT '法兰类型：滑动/平板/盲板/高径',
            remark TEXT NULL COMMENT '备注说明',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_drawing_flange_drawing (drawing_no),
            INDEX idx_drawing_flange_type (flange_type),
            INDEX idx_drawing_flange_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='图纸-法兰类型对照'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS drawing_flange_type_map_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '对照表ID，删除后可空',
            drawing_no VARCHAR(128) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_drawing_flange_log_row (row_id),
            INDEX idx_drawing_flange_log_drawing (drawing_no),
            INDEX idx_drawing_flange_log_action (action),
            INDEX idx_drawing_flange_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='图纸-法兰类型对照操作日志'
    """

SELECT_SQL = """
        id, drawing_no, flange_type, remark,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = ("drawing_no", "flange_type", "remark")

_FLANGE_ALIASES = {
    "滑动": "滑动",
    "so": "滑动",
    "slip": "滑动",
    "slip-on": "滑动",
    "slip on": "滑动",
    "slipon": "滑动",
    "sop": "滑动",
    "soh": "滑动",
    "平板": "平板",
    "pl": "平板",
    "plate": "平板",
    "flat": "平板",
    "盲板": "盲板",
    "bl": "盲板",
    "blind": "盲板",
    "高径": "高径",
    "高颈": "高径",
    "wn": "高径",
    "lwn": "高径",
    "weld neck": "高径",
    "weldneck": "高径",
    "welding neck": "高径",
}


class DrawingFlangeRowIn(BaseModel):
    id: int | None = None
    drawing_no: str = ""
    flange_type: str = ""
    remark: str = ""


class DrawingFlangeSaveRequest(BaseModel):
    rows: list[DrawingFlangeRowIn] = Field(default_factory=list)


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_TABLE_SQL)
    execute(CREATE_LOG_TABLE_SQL)
    _seed_defaults()
    _TABLES_READY = True


def _seed_defaults() -> None:
    """首次建表预置附件对照，已有数据则不动。"""
    existing = fetch_one("SELECT COUNT(*) AS cnt FROM drawing_flange_type_maps")
    if existing and int(existing.get("cnt") or 0) > 0:
        return
    try:
        from app.data.drawing_flange_type_seed import load_default_maps

        seed_rows = load_default_maps()
    except Exception:
        seed_rows = ()

    for drawing_no, flange_type, remark in seed_rows:
        drawing_key = drawing_no_base(drawing_no)
        type_key = normalize_flange_type(flange_type) or DEFAULT_FLANGE_TYPE
        note = _normalize_str(remark)
        if not drawing_key:
            continue
        if _find_by_drawing(drawing_key):
            continue
        new_id = execute(
            """
            INSERT INTO drawing_flange_type_maps (
                drawing_no, flange_type, remark, created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (drawing_key, type_key, note, SEED_OPERATOR, SEED_OPERATOR),
        )
        _write_log(
            action="create",
            operator_name=SEED_OPERATOR,
            row_id=new_id,
            drawing_no=drawing_key,
            change_summary={
                "op": "create",
                "fields": {"drawing_no": drawing_key, "flange_type": type_key, "remark": note},
            },
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_flange_type(raw: str) -> str:
    text = _normalize_str(raw)
    if not text:
        return ""
    if text in FLANGE_TYPES:
        return text
    compact = re.sub(r"[\s_\-]+", " ", text).strip().lower()
    compact = compact.replace("/", " ")
    if compact in _FLANGE_ALIASES:
        return _FLANGE_ALIASES[compact]
    first = compact.split()[0] if compact else ""
    if first in _FLANGE_ALIASES:
        return _FLANGE_ALIASES[first]
    return ""


def normalize_drawing_key(raw: str) -> str:
    return drawing_no_base(_normalize_str(raw))


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "drawing_no": _normalize_str(row.get("drawing_no")),
        "flange_type": _normalize_str(row.get("flange_type")),
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
        "drawing_no": row.get("drawing_no") or "",
        "flange_type": row.get("flange_type") or "",
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
    row_id: int | None,
    drawing_no: str,
    change_summary: dict[str, Any],
    user: CurrentUser | None = None,
    operator_name: str = "",
    operator_user_id: int | None = None,
) -> None:
    execute(
        """
        INSERT INTO drawing_flange_type_map_logs
            (row_id, drawing_no, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            drawing_no,
            action,
            user.id if user else operator_user_id,
            user.name if user else operator_name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_by_drawing(drawing_no: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    key = (drawing_no or "").upper()
    if exclude_id is None:
        return fetch_one(
            f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE UPPER(drawing_no)=%s",
            (key,),
        )
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE UPPER(drawing_no)=%s AND id<>%s",
        (key, exclude_id),
    )


def load_flange_type_map() -> dict[str, str]:
    """生成订单用：去版本图纸号（大写）→ 法兰类型。"""
    _ensure_tables()
    rows = fetch_all("SELECT drawing_no, flange_type FROM drawing_flange_type_maps")
    out: dict[str, str] = {}
    for row in rows:
        key = _normalize_str(row.get("drawing_no")).upper()
        value = _normalize_str(row.get("flange_type"))
        if key and value:
            out[key] = value
    return out


def lookup_flange_type(drawing_no: str, flange_by_drawing: dict[str, str] | None = None) -> str:
    mapping = flange_by_drawing if flange_by_drawing is not None else load_flange_type_map()
    key = normalize_drawing_key(drawing_no).upper()
    if not key:
        return ""
    return mapping.get(key, "")


@router.get("")
def list_drawing_flange_type_maps(
    _user: UserDep,
    drawing_no: str = "",
    flange_type: str = "",
    limit: int = Query(5000, ge=1, le=10000),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = normalize_drawing_key(drawing_no) or _normalize_str(drawing_no)
    if query:
        clauses.append("drawing_no LIKE %s")
        params.append(f"%{query}%")
    type_query = normalize_flange_type(flange_type) or _normalize_str(flange_type)
    if type_query:
        clauses.append("flange_type LIKE %s")
        params.append(f"%{type_query}%")
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM drawing_flange_type_maps
        WHERE {' AND '.join(clauses)}
        ORDER BY drawing_no, id
        LIMIT %s
        """,
        (*params, limit),
    )
    data = [_row_from_db(r) for r in rows]
    return {"status": "success", "total": len(data), "rows": data}


@router.get("/logs")
def list_drawing_flange_type_map_logs(
    _user: UserDep,
    drawing_no: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = normalize_drawing_key(drawing_no) or _normalize_str(drawing_no)
    if query:
        clauses.append("drawing_no LIKE %s")
        params.append(f"%{query}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, drawing_no, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM drawing_flange_type_map_logs
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
def save_drawing_flange_type_maps(req: DrawingFlangeSaveRequest, user: UserDep):
    _ensure_tables()
    prepared: list[tuple[int | None, str, str, str]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(req.rows, start=1):
        drawing_no = normalize_drawing_key(raw.drawing_no)
        flange_type = normalize_flange_type(raw.flange_type) or (
            DEFAULT_FLANGE_TYPE if _normalize_str(raw.flange_type) == "" and drawing_no else ""
        )
        remark = _normalize_str(raw.remark)
        if not drawing_no and not _normalize_str(raw.flange_type) and not remark:
            continue
        if not drawing_no:
            raise HTTPException(status_code=400, detail=f"第{idx}行图纸号不能为空")
        if flange_type not in FLANGE_TYPES:
            raise HTTPException(status_code=400, detail=f"第{idx}行法兰类型必须是：{'、'.join(FLANGE_TYPES)}")
        dup_key = drawing_no.upper()
        if dup_key in seen:
            raise HTTPException(status_code=400, detail=f"本次保存中图纸号重复：{drawing_no}")
        seen.add(dup_key)
        row_id = int(raw.id) if raw.id else None
        prepared.append((row_id, drawing_no, flange_type, remark))

    if not prepared:
        raise HTTPException(status_code=400, detail="没有可保存的行，请至少填写图纸号与法兰类型")

    saved: list[dict[str, Any]] = []
    inserted = 0
    updated = 0
    for row_id, drawing_no, flange_type, remark in prepared:
        if row_id:
            current = fetch_one(
                f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE id=%s",
                (row_id,),
            )
            if not current:
                raise HTTPException(status_code=404, detail=f"未找到对照记录 id={row_id}")
            dup = _find_by_drawing(drawing_no, exclude_id=row_id)
            if dup:
                raise HTTPException(status_code=400, detail=f"该图纸号已存在对照：{drawing_no}")
            before = _row_from_db(current)
            after_snap = {"drawing_no": drawing_no, "flange_type": flange_type, "remark": remark}
            fields = _diff_fields(_snapshot(before), after_snap)
            if not fields:
                saved.append(before)
                continue
            execute(
                """
                UPDATE drawing_flange_type_maps
                SET drawing_no=%s, flange_type=%s, remark=%s,
                    updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (drawing_no, flange_type, remark, user.name, user.id, row_id),
            )
            after = _row_from_db(
                fetch_one(f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE id=%s", (row_id,))
            )
            _write_log(
                action="update",
                user=user,
                row_id=row_id,
                drawing_no=drawing_no,
                change_summary={"op": "update", "fields": fields},
            )
            updated += 1
            saved.append(after)
            continue
        if _find_by_drawing(drawing_no):
            raise HTTPException(status_code=400, detail=f"该图纸号已存在对照：{drawing_no}")
        new_id = execute(
            """
            INSERT INTO drawing_flange_type_maps (
                drawing_no, flange_type, remark,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (drawing_no, flange_type, remark, user.name, user.id, user.name, user.id),
        )
        after = _row_from_db(
            fetch_one(f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE id=%s", (new_id,))
        )
        _write_log(
            action="create",
            user=user,
            row_id=new_id,
            drawing_no=drawing_no,
            change_summary={"op": "create", "fields": _snapshot(after)},
        )
        inserted += 1
        saved.append(after)

    saved.sort(key=lambda item: (str(item.get("drawing_no") or ""), int(item.get("id") or 0)))
    return {
        "status": "success",
        "inserted_count": inserted,
        "updated_count": updated,
        "saved_count": len(saved),
        "rows": saved,
    }


@router.delete("/{row_id}")
def delete_drawing_flange_type_map(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM drawing_flange_type_maps WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到对照记录")
    before = _row_from_db(current)
    execute("DELETE FROM drawing_flange_type_maps WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        drawing_no=before["drawing_no"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}
