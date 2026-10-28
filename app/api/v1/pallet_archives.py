"""托盘建档：独立表与操作日志。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one

router = APIRouter(prefix="/pallet-archives", tags=["托盘建档"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS pallet_archives (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            pallet_name VARCHAR(64) NOT NULL COMMENT '托盘名，如 欧式200',
            pallet_type VARCHAR(64) NOT NULL COMMENT '托盘类型，如 欧式托盘',
            length_mm INT NOT NULL COMMENT '长(mm)',
            width_mm INT NOT NULL COMMENT '宽(mm)',
            height_mm INT NOT NULL COMMENT '高(mm)',
            box_weight_kg DECIMAL(10,2) NOT NULL DEFAULT 20.00 COMMENT '箱重(kg)',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_pallet_archive_name (pallet_name),
            INDEX idx_pallet_archive_type (pallet_type),
            INDEX idx_pallet_archive_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='托盘建档'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS pallet_archive_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '托盘建档ID，删除后可空',
            pallet_name VARCHAR(64) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_pallet_archive_log_row (row_id),
            INDEX idx_pallet_archive_log_name (pallet_name),
            INDEX idx_pallet_archive_log_action (action),
            INDEX idx_pallet_archive_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='托盘建档操作日志'
    """

SELECT_SQL = """
        id, pallet_name, pallet_type, length_mm, width_mm, height_mm, box_weight_kg,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = ("pallet_name", "pallet_type", "length_mm", "width_mm", "height_mm", "box_weight_kg")
INT_KEYS = ("length_mm", "width_mm", "height_mm")
DEFAULT_BOX_WEIGHT_KG = 20.0
ALTER_BOX_WEIGHT_SQL = (
    "ALTER TABLE pallet_archives ADD COLUMN box_weight_kg DECIMAL(10,2) NOT NULL DEFAULT 20.00 "
    "COMMENT '箱重(kg)' AFTER height_mm"
)

DEFAULT_PALLETS = (
    ("欧式200", "欧式托盘", 1200, 800, 200),
    ("欧式400", "欧式托盘", 1200, 800, 400),
    ("周转200", "周转托盘", 1200, 800, 200),
    ("周转400", "周转托盘", 1200, 800, 400),
)

SEED_OPERATOR = "系统预置"


class PalletArchiveRowIn(BaseModel):
    id: int | None = None
    pallet_name: str = ""
    pallet_type: str = ""
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    box_weight_kg: float | None = None


class PalletArchiveSaveRequest(BaseModel):
    rows: list[PalletArchiveRowIn] = Field(default_factory=list)


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_TABLE_SQL)
    execute(CREATE_LOG_TABLE_SQL)
    _ensure_box_weight_column()
    _seed_defaults()
    _TABLES_READY = True


def _ensure_box_weight_column() -> None:
    exists = fetch_one(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'pallet_archives' AND column_name = 'box_weight_kg'
        LIMIT 1
        """
    )
    if not exists:
        execute(ALTER_BOX_WEIGHT_SQL)


def _seed_defaults() -> None:
    """首次建表预置 4 种常用托盘，已有数据则不动。"""
    existing = fetch_one("SELECT COUNT(*) AS cnt FROM pallet_archives")
    if existing and int(existing.get("cnt") or 0) > 0:
        return
    for pallet_name, pallet_type, length_mm, width_mm, height_mm in DEFAULT_PALLETS:
        new_id = execute(
            """
            INSERT INTO pallet_archives (
                pallet_name, pallet_type, length_mm, width_mm, height_mm, box_weight_kg,
                created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                pallet_name,
                pallet_type,
                length_mm,
                width_mm,
                height_mm,
                DEFAULT_BOX_WEIGHT_KG,
                SEED_OPERATOR,
                SEED_OPERATOR,
            ),
        )
        _write_log(
            action="create",
            operator_name=SEED_OPERATOR,
            row_id=new_id,
            pallet_name=pallet_name,
            change_summary={
                "op": "create",
                "fields": {
                    "pallet_name": pallet_name,
                    "pallet_type": pallet_type,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "box_weight_kg": DEFAULT_BOX_WEIGHT_KG,
                },
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


def _required_int(value: Any, row_label: str, field_name: str) -> int:
    if value is None or value == "":
        raise HTTPException(status_code=400, detail=f"{row_label}{field_name}不能为空")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{row_label}{field_name}必须是整数") from None
    if number <= 0:
        raise HTTPException(status_code=400, detail=f"{row_label}{field_name}必须是正整数")
    return number


def _as_weight(value: Any) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return DEFAULT_BOX_WEIGHT_KG


def _parse_weight(value: Any, row_label: str) -> float:
    if value is None or value == "":
        return DEFAULT_BOX_WEIGHT_KG
    try:
        number = round(float(value), 2)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{row_label}箱重必须是数字") from None
    if number < 0:
        raise HTTPException(status_code=400, detail=f"{row_label}箱重不能为负数")
    return number


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "pallet_name": _normalize_str(row.get("pallet_name")),
        "pallet_type": _normalize_str(row.get("pallet_type")),
        "length_mm": int(row.get("length_mm") or 0),
        "width_mm": int(row.get("width_mm") or 0),
        "height_mm": int(row.get("height_mm") or 0),
        "box_weight_kg": _as_weight(row.get("box_weight_kg") if row.get("box_weight_kg") is not None else DEFAULT_BOX_WEIGHT_KG),
        "created_by": _normalize_str(row.get("created_by")),
        "created_by_user_id": row.get("created_by_user_id"),
        "created_at": _jsonable(row.get("created_at")) or "",
        "updated_by": _normalize_str(row.get("updated_by")),
        "updated_by_user_id": row.get("updated_by_user_id"),
        "updated_at": _jsonable(row.get("updated_at")) or "",
    }


def _snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "pallet_name": row.get("pallet_name") or "",
        "pallet_type": row.get("pallet_type") or "",
        "length_mm": int(row.get("length_mm") or 0),
        "width_mm": int(row.get("width_mm") or 0),
        "height_mm": int(row.get("height_mm") or 0),
        "box_weight_kg": _as_weight(row.get("box_weight_kg") if row.get("box_weight_kg") is not None else DEFAULT_BOX_WEIGHT_KG),
    }


def _diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in DIFF_KEYS:
        left = before.get(key)
        right = after.get(key)
        if key in INT_KEYS:
            left = int(left or 0)
            right = int(right or 0)
        elif key == "box_weight_kg":
            left = _as_weight(left)
            right = _as_weight(right)
        else:
            left = _normalize_str(left)
            right = _normalize_str(right)
        if left != right:
            out[key] = {"before": left, "after": right}
    return out


def _write_log(
    *,
    action: str,
    row_id: int | None,
    pallet_name: str,
    change_summary: dict[str, Any],
    user: CurrentUser | None = None,
    operator_name: str = "",
    operator_user_id: int | None = None,
) -> None:
    execute(
        """
        INSERT INTO pallet_archive_logs
            (row_id, pallet_name, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            pallet_name,
            action,
            user.id if user else operator_user_id,
            user.name if user else operator_name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_by_name(pallet_name: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(f"SELECT {SELECT_SQL} FROM pallet_archives WHERE pallet_name=%s", (pallet_name,))
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM pallet_archives WHERE pallet_name=%s AND id<>%s",
        (pallet_name, exclude_id),
    )


def load_pallet_archives() -> list[dict[str, Any]]:
    """装箱等模块读取托盘主数据。"""
    _ensure_tables()
    rows = fetch_all(f"SELECT {SELECT_SQL} FROM pallet_archives ORDER BY id")
    return [_row_from_db(r) for r in rows]


@router.get("")
def list_pallet_archives(
    _user: UserDep,
    pallet_name: str = "",
    pallet_type: str = "",
    limit: int = Query(2000, ge=1, le=5000),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    name_query = _normalize_str(pallet_name)
    type_query = _normalize_str(pallet_type)
    if name_query:
        clauses.append("pallet_name LIKE %s")
        params.append(f"%{name_query}%")
    if type_query:
        clauses.append("pallet_type LIKE %s")
        params.append(f"%{type_query}%")
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM pallet_archives
        WHERE {' AND '.join(clauses)}
        ORDER BY id
        LIMIT %s
        """,
        (*params, limit),
    )
    data = [_row_from_db(r) for r in rows]
    return {"status": "success", "total": len(data), "rows": data}


@router.get("/logs")
def list_pallet_archive_logs(
    _user: UserDep,
    pallet_name: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    name_query = _normalize_str(pallet_name)
    if name_query:
        clauses.append("pallet_name LIKE %s")
        params.append(f"%{name_query}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, pallet_name, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM pallet_archive_logs
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
def save_pallet_archives(req: PalletArchiveSaveRequest, user: UserDep):
    _ensure_tables()
    prepared: list[dict[str, Any]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(req.rows, start=1):
        label = f"第{idx}行 "
        pallet_name = _normalize_str(raw.pallet_name)
        pallet_type = _normalize_str(raw.pallet_type)
        empty_size = raw.length_mm is None and raw.width_mm is None and raw.height_mm is None
        if not pallet_name and not pallet_type and empty_size:
            continue
        if not pallet_name:
            raise HTTPException(status_code=400, detail=f"{label}托盘名不能为空")
        if not pallet_type:
            raise HTTPException(status_code=400, detail=f"{label}托盘类型不能为空")
        length_mm = _required_int(raw.length_mm, label, "长")
        width_mm = _required_int(raw.width_mm, label, "宽")
        height_mm = _required_int(raw.height_mm, label, "高")
        box_weight_kg = _parse_weight(raw.box_weight_kg, label)
        if pallet_name in seen:
            raise HTTPException(status_code=400, detail=f"本次保存中托盘名重复：{pallet_name}")
        seen.add(pallet_name)
        prepared.append(
            {
                "id": int(raw.id) if raw.id else None,
                "pallet_name": pallet_name,
                "pallet_type": pallet_type,
                "length_mm": length_mm,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "box_weight_kg": box_weight_kg,
            }
        )

    if not prepared:
        raise HTTPException(status_code=400, detail="没有可保存的行，请至少填写托盘名、类型和长宽高")

    saved: list[dict[str, Any]] = []
    inserted = 0
    updated = 0
    for item in prepared:
        row_id = item["id"]
        if row_id:
            current = fetch_one(f"SELECT {SELECT_SQL} FROM pallet_archives WHERE id=%s", (row_id,))
            if not current:
                raise HTTPException(status_code=404, detail=f"未找到托盘建档记录 id={row_id}")
            dup = _find_by_name(item["pallet_name"], exclude_id=row_id)
            if dup:
                raise HTTPException(status_code=400, detail=f"该托盘名已存在：{item['pallet_name']}")
            before = _row_from_db(current)
            fields = _diff_fields(_snapshot(before), item)
            if not fields:
                saved.append(before)
                continue
            execute(
                """
                UPDATE pallet_archives
                SET pallet_name=%s, pallet_type=%s, length_mm=%s, width_mm=%s, height_mm=%s,
                    box_weight_kg=%s, updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (
                    item["pallet_name"],
                    item["pallet_type"],
                    item["length_mm"],
                    item["width_mm"],
                    item["height_mm"],
                    item["box_weight_kg"],
                    user.name,
                    user.id,
                    row_id,
                ),
            )
            after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM pallet_archives WHERE id=%s", (row_id,)))
            _write_log(
                action="update",
                user=user,
                row_id=row_id,
                pallet_name=item["pallet_name"],
                change_summary={"op": "update", "fields": fields},
            )
            updated += 1
            saved.append(after)
            continue
        if _find_by_name(item["pallet_name"]):
            raise HTTPException(status_code=400, detail=f"该托盘名已存在：{item['pallet_name']}")
        new_id = execute(
            """
            INSERT INTO pallet_archives (
                pallet_name, pallet_type, length_mm, width_mm, height_mm, box_weight_kg,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item["pallet_name"],
                item["pallet_type"],
                item["length_mm"],
                item["width_mm"],
                item["height_mm"],
                item["box_weight_kg"],
                user.name,
                user.id,
                user.name,
                user.id,
            ),
        )
        after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM pallet_archives WHERE id=%s", (new_id,)))
        _write_log(
            action="create",
            user=user,
            row_id=new_id,
            pallet_name=item["pallet_name"],
            change_summary={"op": "create", "fields": _snapshot(after)},
        )
        inserted += 1
        saved.append(after)

    return {
        "status": "success",
        "inserted_count": inserted,
        "updated_count": updated,
        "saved_count": len(saved),
        "rows": saved,
    }


@router.delete("/{row_id}")
def delete_pallet_archive(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM pallet_archives WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到托盘建档记录")
    before = _row_from_db(current)
    execute("DELETE FROM pallet_archives WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        pallet_name=before["pallet_name"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}
