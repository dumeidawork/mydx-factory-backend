"""法国自动装箱：物料-数量/托对照，独立主表与操作日志。"""
from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime
from typing import Any, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.api.v1.dn_inch_maps import normalize_dn_value
from app.api.v1.pallet_archives import load_pallet_archives
from app.core.database import execute, fetch_all, fetch_one
from app.utils.spec_parser import extract_spec_model_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/france-auto-packing-qty-maps", tags=["法国自动装箱-物料数量/托"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

FRANCE_MAP_TABLE = "france_auto_packing_qty_maps"
FRANCE_LOG_TABLE = "france_auto_packing_qty_map_logs"
DALIAN_MAP_TABLE = "dalian_auto_packing_qty_maps"
DALIAN_LOG_TABLE = "dalian_auto_packing_qty_map_logs"
_MAP_TABLES = frozenset({FRANCE_MAP_TABLE, DALIAN_MAP_TABLE})
_LOG_TABLES = frozenset({FRANCE_LOG_TABLE, DALIAN_LOG_TABLE})


class QtyMapScope(NamedTuple):
    map_table: str
    log_table: str
    copy_from: str | None


FRANCE_SCOPE = QtyMapScope(FRANCE_MAP_TABLE, FRANCE_LOG_TABLE, None)
DALIAN_SCOPE = QtyMapScope(DALIAN_MAP_TABLE, DALIAN_LOG_TABLE, FRANCE_MAP_TABLE)
_SCOPE: ContextVar[QtyMapScope] = ContextVar("qty_map_scope", default=FRANCE_SCOPE)
_TABLES_READY: set[str] = set()


def _map_table() -> str:
    name = _SCOPE.get().map_table
    if name not in _MAP_TABLES:
        raise RuntimeError("invalid qty map table")
    return name


def _log_table() -> str:
    name = _SCOPE.get().log_table
    if name not in _LOG_TABLES:
        raise RuntimeError("invalid qty map log table")
    return name


def call_in_dalian_scope(func, *args, **kwargs):
    """在同一次调用里切换到大连对照表。不要做成依赖生成器，线程池进出不在同一个 Context。"""
    token = _SCOPE.set(DALIAN_SCOPE)
    try:
        return func(*args, **kwargs)
    finally:
        _SCOPE.reset(token)


def list_dalian_qty_maps(
    _user: UserDep,
    material_no: str = "",
    dn_value: str = "",
    limit: int = Query(2000, ge=1, le=5000),
):
    return call_in_dalian_scope(list_qty_maps, _user, material_no, dn_value, limit)


def list_dalian_qty_map_logs(
    _user: UserDep,
    material_no: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    return call_in_dalian_scope(list_qty_map_logs, _user, material_no, action, limit)


def lookup_dalian_material_dn(_user: UserDep, material_no: str = ""):
    return call_in_dalian_scope(lookup_material_dn, _user, material_no)


def save_dalian_qty_maps(req: QtyMapSaveRequest, user: UserDep):
    return call_in_dalian_scope(save_qty_maps, req, user)


def align_dalian_qty_map_dn(user: UserDep):
    return call_in_dalian_scope(align_qty_map_dn, user)


def delete_dalian_qty_map(row_id: int, user: UserDep):
    return call_in_dalian_scope(delete_qty_map, row_id, user)

GENERIC_PALLETS = ("欧式托盘", "实木托盘")
DEFAULT_PALLET_TYPE = "欧式托盘"
SEED_OPERATOR = "系统预置"

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS france_auto_packing_qty_maps (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            material_no VARCHAR(100) NOT NULL COMMENT '物料号',
            dn_value VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'DN，可从物料表带出或手输',
            qty_per_pallet INT NOT NULL COMMENT '数量/托',
            pallet_type VARCHAR(64) NOT NULL COMMENT '托盘：欧式托盘/实木托盘或托盘建档名称',
            length_mm INT NULL COMMENT '长',
            width_mm INT NULL COMMENT '宽',
            height_mm INT NULL COMMENT '高',
            remark TEXT NULL COMMENT '备注',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_fr_auto_pack_qty_mat_pallet (material_no, pallet_type),
            INDEX idx_fr_auto_pack_qty_material (material_no),
            INDEX idx_fr_auto_pack_qty_dn (dn_value),
            INDEX idx_fr_auto_pack_qty_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国自动装箱-物料数量/托对照'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS france_auto_packing_qty_map_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '对照表ID，删除后可空',
            material_no VARCHAR(100) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete/upload/export',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_fr_auto_pack_qty_log_row (row_id),
            INDEX idx_fr_auto_pack_qty_log_mat (material_no),
            INDEX idx_fr_auto_pack_qty_log_action (action),
            INDEX idx_fr_auto_pack_qty_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='法国自动装箱-物料数量/托操作日志'
    """

CREATE_NOTICE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS workbench_qty_map_dn_notices (
            id INT PRIMARY KEY AUTO_INCREMENT,
            owner_user_id INT NOT NULL COMMENT '通知接收人',
            owner_name VARCHAR(64) NULL,
            material_nos TEXT NOT NULL COMMENT '待补 DN 的物料号 JSON 数组',
            status VARCHAR(16) NOT NULL DEFAULT '待确认' COMMENT '待确认/已确认',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            confirmed_at DATETIME NULL,
            confirmed_by_user_id INT NULL,
            INDEX idx_qty_map_dn_notice_owner_status (owner_user_id, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='物料-数量/托缺 DN 工作台通知'
    """

NOTICE_STATUS_PENDING = "待确认"
NOTICE_STATUS_CONFIRMED = "已确认"

SELECT_SQL = """
        id, material_no, dn_value, qty_per_pallet, pallet_type,
        length_mm, width_mm, height_mm, remark,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = (
    "material_no",
    "dn_value",
    "qty_per_pallet",
    "pallet_type",
    "length_mm",
    "width_mm",
    "height_mm",
    "remark",
)

_DN_IN_TEXT = re.compile(r"DN\s*(\d+)", re.I)
_NB_IN_TEXT = re.compile(r"(\d+)\s*NB", re.I)


class QtyMapRowIn(BaseModel):
    id: int | None = None
    material_no: str = ""
    dn_value: str = ""
    qty_per_pallet: int | None = None
    pallet_type: str = ""
    length_mm: int | None = None
    width_mm: int | None = None
    height_mm: int | None = None
    remark: str = ""


class QtyMapSaveRequest(BaseModel):
    rows: list[QtyMapRowIn] = Field(default_factory=list)


def _create_map_sql(table: str, comment: str, unique_key: str) -> str:
    if table not in _MAP_TABLES:
        raise RuntimeError("invalid qty map table")
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            material_no VARCHAR(100) NOT NULL COMMENT '物料号',
            dn_value VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'DN，可从物料表带出或手输',
            qty_per_pallet INT NOT NULL COMMENT '数量/托',
            pallet_type VARCHAR(64) NOT NULL COMMENT '托盘：欧式托盘/实木托盘或托盘建档名称',
            length_mm INT NULL COMMENT '长',
            width_mm INT NULL COMMENT '宽',
            height_mm INT NULL COMMENT '高',
            remark TEXT NULL COMMENT '备注',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY {unique_key} (material_no, pallet_type),
            INDEX idx_qty_material (material_no),
            INDEX idx_qty_dn (dn_value),
            INDEX idx_qty_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='{comment}'
    """


def _create_log_sql(table: str, comment: str) -> str:
    if table not in _LOG_TABLES:
        raise RuntimeError("invalid qty map log table")
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '对照表ID，删除后可空',
            material_no VARCHAR(100) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete/upload/export',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_qty_log_row (row_id),
            INDEX idx_qty_log_mat (material_no),
            INDEX idx_qty_log_action (action),
            INDEX idx_qty_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='{comment}'
    """


def _ensure_tables(*, seed: bool = True) -> None:
    scope = _SCOPE.get()
    table = _map_table()
    if table not in _TABLES_READY:
        if scope.copy_from:
            execute(_create_map_sql(table, "大连自动装箱-物料数量/托对照", "uk_dl_auto_pack_qty_mat_pallet"))
            execute(_create_log_sql(_log_table(), "大连自动装箱-物料数量/托操作日志"))
        else:
            execute(CREATE_TABLE_SQL)
            execute(CREATE_LOG_TABLE_SQL)
            execute(CREATE_NOTICE_TABLE_SQL)
        col = fetch_one(
            """
            SELECT CHARACTER_MAXIMUM_LENGTH AS len
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = %s
              AND column_name = 'pallet_type'
            """,
            (table,),
        )
        if col and int(col.get("len") or 0) < 64:
            execute(
                f"""
                ALTER TABLE {table}
                MODIFY COLUMN pallet_type VARCHAR(64) NOT NULL
                COMMENT '托盘：欧式托盘/实木托盘或托盘建档名称'
                """
            )
        _TABLES_READY.add(table)
    if seed:
        _seed_defaults()


def ensure_dalian_qty_maps() -> None:
    token = _SCOPE.set(DALIAN_SCOPE)
    try:
        _ensure_tables(seed=True)
    finally:
        _SCOPE.reset(token)


def _seed_defaults() -> None:
    """法国表为空时灌入预置；大连表为空时从法国表现状复制一次，之后互不影响。"""
    table = _map_table()
    existing = fetch_one(f"SELECT COUNT(*) AS cnt FROM {table}")
    if existing and int(existing.get("cnt") or 0) > 0:
        return
    source = _SCOPE.get().copy_from
    if source:
        _copy_qty_maps_from(source, table)
        return
    try:
        from app.data.france_auto_packing_qty_seed import load_default_maps

        seed_rows = load_default_maps()
    except Exception:
        seed_rows = ()
    for row in seed_rows:
        insert_qty_seed_row(*row)


def _copy_qty_maps_from(source: str, target: str) -> None:
    if source not in _MAP_TABLES or target not in _MAP_TABLES:
        raise RuntimeError("invalid qty map copy")
    token = _SCOPE.set(FRANCE_SCOPE)
    try:
        _ensure_tables(seed=True)
    finally:
        _SCOPE.reset(token)
    source_count = fetch_one(f"SELECT COUNT(*) AS cnt FROM {source}")
    row_count = int((source_count or {}).get("cnt") or 0)
    if row_count <= 0:
        return
    execute(
        f"""
        INSERT INTO {target} (
            material_no, dn_value, qty_per_pallet, pallet_type,
            length_mm, width_mm, height_mm, remark,
            created_by, created_by_user_id, updated_by, updated_by_user_id
        )
        SELECT
            material_no, dn_value, qty_per_pallet, pallet_type,
            length_mm, width_mm, height_mm, remark,
            created_by, created_by_user_id, updated_by, updated_by_user_id
        FROM {source}
        """
    )
    execute(
        f"""
        INSERT INTO {_log_table()}
            (row_id, material_no, action, operator_name, change_summary)
        VALUES (NULL, '', 'upload', %s, %s)
        """,
        (
            SEED_OPERATOR,
            json.dumps({"op": "copy", "from": source, "rows": row_count}, ensure_ascii=False),
        ),
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="长/宽/高必须是整数") from None
    return number


def _required_qty(value: Any, row_label: str) -> int:
    if value is None or value == "":
        raise HTTPException(status_code=400, detail=f"{row_label} 数量/托不能为空")
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{row_label} 数量/托必须是整数") from None
    if qty <= 0:
        raise HTTPException(status_code=400, detail=f"{row_label} 数量/托必须大于 0")
    return qty


def _pallet_archive_by_name() -> dict[str, dict[str, Any]]:
    return {row["pallet_name"]: row for row in load_pallet_archives() if row.get("pallet_name")}


def _normalize_pallet(raw: str, row_label: str, archives: dict[str, dict[str, Any]]) -> str:
    text = _normalize_str(raw)
    if not text:
        raise HTTPException(status_code=400, detail=f"{row_label} 托盘不能为空")
    if text in GENERIC_PALLETS or text in archives:
        return text
    raise HTTPException(status_code=400, detail=f"{row_label} 托盘不在可选列表中：{text}")


def _apply_archive_dims(
    pallet: str,
    length_mm: int | None,
    width_mm: int | None,
    height_mm: int | None,
    archives: dict[str, dict[str, Any]],
) -> tuple[int | None, int | None, int | None]:
    if pallet in GENERIC_PALLETS:
        return length_mm, width_mm, height_mm
    archive = archives.get(pallet)
    if not archive:
        return length_mm, width_mm, height_mm
    if length_mm is None:
        length_mm = archive.get("length_mm")
    if width_mm is None:
        width_mm = archive.get("width_mm")
    if height_mm is None:
        height_mm = archive.get("height_mm")
    return length_mm, width_mm, height_mm


def _dn_from_material_row(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    spec = _normalize_str(row.get("spec"))
    if spec:
        return normalize_dn_value(spec)
    fields = extract_spec_model_fields(_normalize_str(row.get("spec_model")))
    if fields.get("spec"):
        return normalize_dn_value(fields["spec"])
    blob = _normalize_str(row.get("spec_model"))
    match = _DN_IN_TEXT.search(blob)
    if match:
        return f"DN{int(match.group(1))}"
    match = _NB_IN_TEXT.search(blob)
    if match:
        return f"DN{int(match.group(1))}"
    return ""


def lookup_dn_by_material_no(material_no: str) -> str:
    key = _normalize_str(material_no)
    if not key:
        return ""
    found = lookup_dn_by_material_nos([key])
    return found.get(key, "")


def lookup_dn_by_material_nos(material_nos: list[str]) -> dict[str, str]:
    keys = []
    seen: set[str] = set()
    for item in material_nos:
        key = _normalize_str(item)
        if not key or key in seen:
            continue
        seen.add(key)
        keys.append(key)
    if not keys:
        return {}
    placeholders = ", ".join(["%s"] * len(keys))
    rows = fetch_all(
        f"""
        SELECT material_no, spec, spec_model
        FROM materials
        WHERE material_no IN ({placeholders})
        ORDER BY (CASE WHEN spec IS NOT NULL AND spec <> '' THEN 0 ELSE 1 END), id DESC
        """,
        tuple(keys),
    )
    out: dict[str, str] = {}
    for row in rows:
        key = _normalize_str(row.get("material_no"))
        if not key or key in out:
            continue
        dn_value = _dn_from_material_row(row)
        if dn_value:
            out[key] = dn_value
    return out


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "material_no": _normalize_str(row.get("material_no")),
        "dn_value": _normalize_str(row.get("dn_value")),
        "qty_per_pallet": int(row.get("qty_per_pallet") or 0),
        "pallet_type": _normalize_str(row.get("pallet_type")),
        "length_mm": row.get("length_mm"),
        "width_mm": row.get("width_mm"),
        "height_mm": row.get("height_mm"),
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
        "material_no": row.get("material_no") or "",
        "dn_value": row.get("dn_value") or "",
        "qty_per_pallet": row.get("qty_per_pallet"),
        "pallet_type": row.get("pallet_type") or "",
        "length_mm": row.get("length_mm"),
        "width_mm": row.get("width_mm"),
        "height_mm": row.get("height_mm"),
        "remark": row.get("remark") or "",
    }


def _norm_diff(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _diff_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key in DIFF_KEYS:
        left = _norm_diff(before.get(key))
        right = _norm_diff(after.get(key))
        if left != right:
            out[key] = {"before": before.get(key), "after": after.get(key)}
    return out


def _write_log(
    *,
    action: str,
    row_id: int | None,
    material_no: str,
    change_summary: dict[str, Any],
    user: CurrentUser | None = None,
    operator_name: str = "",
    operator_user_id: int | None = None,
) -> None:
    execute(
        f"""
        INSERT INTO {_log_table()}
            (row_id, material_no, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            material_no,
            action,
            user.id if user else operator_user_id,
            user.name if user else operator_name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_dup(material_no: str, pallet_type: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(
            f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE material_no=%s AND pallet_type=%s",
            (material_no, pallet_type),
        )
    return fetch_one(
        f"""
        SELECT {SELECT_SQL} FROM {_map_table()}
        WHERE material_no=%s AND pallet_type=%s AND id<>%s
        """,
        (material_no, pallet_type, exclude_id),
    )


def insert_qty_seed_row(
    material_no: str,
    qty_per_pallet: int,
    pallet_type: str,
    length_mm: int | None,
    width_mm: int | None,
    height_mm: int | None,
    remark: str,
    *,
    dn_value: str = "",
) -> int | None:
    material_key = _normalize_str(material_no)
    pallet_key = _normalize_str(pallet_type)
    note = _normalize_str(remark)
    qty = int(qty_per_pallet or 0)
    if not material_key or not pallet_key or qty <= 0:
        return None
    if _find_dup(material_key, pallet_key):
        return None
    dn_key = normalize_dn_value(dn_value) if dn_value else ""
    if not dn_key:
        try:
            dn_key = lookup_dn_by_material_no(material_key)
        except Exception:
            dn_key = ""
    new_id = execute(
        f"""
        INSERT INTO {_map_table()} (
            material_no, dn_value, qty_per_pallet, pallet_type,
            length_mm, width_mm, height_mm, remark,
            created_by, updated_by
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            material_key,
            dn_key,
            qty,
            pallet_key,
            length_mm,
            width_mm,
            height_mm,
            note,
            SEED_OPERATOR,
            SEED_OPERATOR,
        ),
    )
    _write_log(
        action="create",
        operator_name=SEED_OPERATOR,
        row_id=new_id,
        material_no=material_key,
        change_summary={
            "op": "create",
            "fields": {
                "material_no": material_key,
                "dn_value": dn_key,
                "qty_per_pallet": qty,
                "pallet_type": pallet_key,
                "length_mm": length_mm,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "remark": note,
            },
        },
    )
    return new_id


def _parse_notice_materials(raw: Any) -> list[str]:
    if isinstance(raw, list):
        values = raw
    else:
        text = _normalize_str(raw)
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        values = parsed if isinstance(parsed, list) else []
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        key = _normalize_str(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _notice_row(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    materials = _parse_notice_materials(row.get("material_nos"))
    return {
        "id": int(row["id"]),
        "owner_user_id": row.get("owner_user_id"),
        "owner_name": _normalize_str(row.get("owner_name")),
        "material_nos": materials,
        "missing_count": len(materials),
        "status": _normalize_str(row.get("status")) or NOTICE_STATUS_PENDING,
        "created_at": _jsonable(row.get("created_at")) or "",
        "updated_at": _jsonable(row.get("updated_at")) or "",
        "hint": (
            f"请上传以下 {len(materials)} 个物料号的图纸信息/物料信息，后批量对齐【物料-数量/托】表的 DN"
            if materials
            else ""
        ),
    }


def _find_pending_notice(owner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT id, owner_user_id, owner_name, material_nos, status, created_at, updated_at
        FROM workbench_qty_map_dn_notices
        WHERE owner_user_id=%s AND status=%s
        ORDER BY id DESC
        LIMIT 1
        """,
        (owner_user_id, NOTICE_STATUS_PENDING),
    )


def _confirm_notice(notice_id: int, user_id: int | None = None) -> None:
    execute(
        """
        UPDATE workbench_qty_map_dn_notices
        SET status=%s, confirmed_at=NOW(), confirmed_by_user_id=%s, material_nos=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (NOTICE_STATUS_CONFIRMED, user_id, "[]", notice_id),
    )


def upsert_qty_map_dn_notice(user: CurrentUser, missing: list[str]) -> dict[str, Any] | None:
    _ensure_tables(seed=False)
    pending = _find_pending_notice(int(user.id))
    unique = _parse_notice_materials(missing)
    if not unique:
        if pending:
            _confirm_notice(int(pending["id"]), int(user.id))
        return None
    payload = json.dumps(unique, ensure_ascii=False)
    if pending:
        execute(
            """
            UPDATE workbench_qty_map_dn_notices
            SET material_nos=%s, owner_name=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (payload, user.name, int(pending["id"])),
        )
        row = fetch_one(
            """
            SELECT id, owner_user_id, owner_name, material_nos, status, created_at, updated_at
            FROM workbench_qty_map_dn_notices
            WHERE id=%s
            """,
            (int(pending["id"]),),
        )
        return _notice_row(row)
    new_id = execute(
        """
        INSERT INTO workbench_qty_map_dn_notices (owner_user_id, owner_name, material_nos, status)
        VALUES (%s, %s, %s, %s)
        """,
        (int(user.id), user.name, payload, NOTICE_STATUS_PENDING),
    )
    row = fetch_one(
        """
        SELECT id, owner_user_id, owner_name, material_nos, status, created_at, updated_at
        FROM workbench_qty_map_dn_notices
        WHERE id=%s
        """,
        (new_id,),
    )
    return _notice_row(row)


def prune_qty_map_dn_notices() -> None:
    _ensure_tables(seed=False)
    rows = fetch_all(
        """
        SELECT id, material_nos
        FROM workbench_qty_map_dn_notices
        WHERE status=%s
        """,
        (NOTICE_STATUS_PENDING,),
    )
    for row in rows:
        remaining = [
            material_no
            for material_no in _parse_notice_materials(row.get("material_nos"))
            if material_no
        ]
        found = lookup_dn_by_material_nos(remaining)
        leftover = [material_no for material_no in remaining if material_no not in found]
        if leftover:
            execute(
                """
                UPDATE workbench_qty_map_dn_notices
                SET material_nos=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (json.dumps(leftover, ensure_ascii=False), int(row["id"])),
            )
        else:
            _confirm_notice(int(row["id"]))


def get_pending_qty_map_dn_notice_items(user_id: int) -> list[dict[str, Any]]:
    _ensure_tables(seed=False)
    prune_qty_map_dn_notices()
    row = _find_pending_notice(int(user_id))
    item = _notice_row(row)
    if not item or not item.get("material_nos"):
        return []
    return [item]


def _update_row_dn(
    row: dict[str, Any],
    dn_value: str,
    *,
    user: CurrentUser | None = None,
    operator_name: str = "",
) -> None:
    row_id = int(row["id"])
    before = _row_from_db(row)
    execute(
        f"""
        UPDATE {_map_table()}
        SET dn_value=%s, updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (
            dn_value,
            user.name if user else (operator_name or SEED_OPERATOR),
            user.id if user else None,
            row_id,
        ),
    )
    after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE id=%s", (row_id,)))
    _write_log(
        action="align",
        user=user,
        operator_name="" if user else (operator_name or SEED_OPERATOR),
        row_id=row_id,
        material_no=after["material_no"],
        change_summary={"op": "align", "fields": _diff_fields(_snapshot(before), _snapshot(after))},
    )


def sync_qty_map_dn(
    *,
    user: CurrentUser | None = None,
    material_nos: list[str] | None = None,
    write_notice: bool = False,
    operator_name: str = "",
) -> dict[str, Any]:
    _ensure_tables()
    keys = _parse_notice_materials(material_nos or [])
    clauses = ["1=1"]
    params: list[Any] = []
    if keys:
        placeholders = ", ".join(["%s"] * len(keys))
        clauses.append(f"material_no IN ({placeholders})")
        params.extend(keys)
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM {_map_table()}
        WHERE {' AND '.join(clauses)}
        ORDER BY id
        """,
        tuple(params),
    )
    material_keys = [
        _normalize_str(row.get("material_no"))
        for row in rows
        if _normalize_str(row.get("material_no"))
    ]
    dn_cache = lookup_dn_by_material_nos(material_keys)
    updated_rows = 0
    missing: list[str] = []
    seen_missing: set[str] = set()
    for row in rows:
        material_no = _normalize_str(row.get("material_no"))
        if not material_no:
            continue
        dn_value = dn_cache.get(material_no, "")
        if not dn_value:
            if material_no not in seen_missing:
                seen_missing.add(material_no)
                missing.append(material_no)
            continue
        current = _normalize_str(row.get("dn_value"))
        if current == dn_value:
            continue
        _update_row_dn(row, dn_value, user=user, operator_name=operator_name)
        updated_rows += 1
    notice = None
    if write_notice and user and _SCOPE.get().map_table == FRANCE_MAP_TABLE:
        notice = upsert_qty_map_dn_notice(user, missing)
    else:
        prune_qty_map_dn_notices()
    return {
        "status": "success",
        "checked_count": len(rows),
        "updated_count": updated_rows,
        "missing_count": len(missing),
        "missing_materials": missing,
        "notice": notice,
    }


def apply_qty_map_dn_from_materials(material_nos: list[str], operator_name: str = "") -> dict[str, Any]:
    """物料/图纸写入后回填对照表 DN，并清掉已能取到 DN 的工作台待办。失败不抛。"""
    try:
        keys = _parse_notice_materials(material_nos)
        if not keys:
            prune_qty_map_dn_notices()
            return {"status": "success", "updated_count": 0, "missing_count": 0, "missing_materials": []}
        result = sync_qty_map_dn(material_nos=keys, write_notice=False, operator_name=operator_name)
        token = _SCOPE.set(DALIAN_SCOPE)
        try:
            sync_qty_map_dn(material_nos=keys, write_notice=False, operator_name=operator_name)
        finally:
            _SCOPE.reset(token)
        return result
    except Exception:
        logger.exception("apply qty map dn from materials failed")
        return {"status": "error", "updated_count": 0, "missing_count": 0, "missing_materials": []}


def _row_is_blank(raw: QtyMapRowIn) -> bool:
    return not any(
        [
            _normalize_str(raw.material_no),
            _normalize_str(raw.dn_value),
            raw.qty_per_pallet not in (None, ""),
            _normalize_str(raw.pallet_type) and _normalize_str(raw.pallet_type) != DEFAULT_PALLET_TYPE,
            raw.length_mm not in (None, ""),
            raw.width_mm not in (None, ""),
            raw.height_mm not in (None, ""),
            _normalize_str(raw.remark),
        ]
    )


@router.get("")
def list_qty_maps(
    _user: UserDep,
    material_no: str = "",
    dn_value: str = "",
    limit: int = Query(2000, ge=1, le=5000),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    mat = _normalize_str(material_no)
    if mat:
        clauses.append("material_no LIKE %s")
        params.append(f"%{mat}%")
    dn = normalize_dn_value(dn_value) or _normalize_str(dn_value)
    if dn:
        clauses.append("dn_value LIKE %s")
        params.append(f"%{dn}%")
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM {_map_table()}
        WHERE {' AND '.join(clauses)}
        ORDER BY id
        LIMIT %s
        """,
        (*params, limit),
    )
    return {"status": "success", "total": len(rows), "rows": [_row_from_db(r) for r in rows]}


@router.get("/logs")
def list_qty_map_logs(
    _user: UserDep,
    material_no: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    mat = _normalize_str(material_no)
    if mat:
        clauses.append("material_no LIKE %s")
        params.append(f"%{mat}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, material_no, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM {_log_table()}
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


@router.get("/material-dn")
def lookup_material_dn(_user: UserDep, material_no: str = ""):
    _ensure_tables()
    key = _normalize_str(material_no)
    if not key:
        raise HTTPException(status_code=400, detail="请先填写物料号")
    dn_value = lookup_dn_by_material_no(key)
    return {"status": "success", "material_no": key, "dn_value": dn_value, "found": bool(dn_value)}


@router.post("/save")
def save_qty_maps(req: QtyMapSaveRequest, user: UserDep):
    _ensure_tables()
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    archives = _pallet_archive_by_name()
    for idx, raw in enumerate(req.rows, start=1):
        if _row_is_blank(raw):
            continue
        label = f"第{idx}行"
        material_no = _normalize_str(raw.material_no)
        if not material_no:
            raise HTTPException(status_code=400, detail=f"{label} 物料号不能为空")
        pallet_type = _normalize_pallet(raw.pallet_type, label, archives)
        qty = _required_qty(raw.qty_per_pallet, label)
        length_mm, width_mm, height_mm = _apply_archive_dims(
            pallet_type,
            _optional_int(raw.length_mm),
            _optional_int(raw.width_mm),
            _optional_int(raw.height_mm),
            archives,
        )
        for name, value in (("长", length_mm), ("宽", width_mm), ("高", height_mm)):
            if value is not None and value < 0:
                raise HTTPException(status_code=400, detail=f"{label} {name}不能为负数")
        dn_value = normalize_dn_value(raw.dn_value) or lookup_dn_by_material_no(material_no)
        key = (material_no, pallet_type)
        if key in seen:
            raise HTTPException(status_code=400, detail=f"本次保存中物料号+托盘重复：{material_no} / {pallet_type}")
        seen.add(key)
        prepared.append(
            {
                "id": int(raw.id) if raw.id else None,
                "material_no": material_no,
                "dn_value": dn_value,
                "qty_per_pallet": qty,
                "pallet_type": pallet_type,
                "length_mm": length_mm,
                "width_mm": width_mm,
                "height_mm": height_mm,
                "remark": _normalize_str(raw.remark),
            }
        )

    if not prepared:
        raise HTTPException(status_code=400, detail="没有可保存的行，请至少填写物料号、数量/托与托盘")

    saved: list[dict[str, Any]] = []
    inserted = 0
    updated = 0
    for item in prepared:
        row_id = item["id"]
        if row_id:
            current = fetch_one(f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE id=%s", (row_id,))
            if not current:
                raise HTTPException(status_code=404, detail=f"未找到对照记录 id={row_id}")
            dup = _find_dup(item["material_no"], item["pallet_type"], exclude_id=row_id)
            if dup:
                raise HTTPException(
                    status_code=400,
                    detail=f"该物料号+托盘已存在对照：{item['material_no']} / {item['pallet_type']}",
                )
            before = _row_from_db(current)
            after_snap = _snapshot(item)
            fields = _diff_fields(_snapshot(before), after_snap)
            if not fields:
                saved.append(before)
                continue
            execute(
                f"""
                UPDATE {_map_table()}
                SET material_no=%s, dn_value=%s, qty_per_pallet=%s, pallet_type=%s,
                    length_mm=%s, width_mm=%s, height_mm=%s, remark=%s,
                    updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (
                    item["material_no"],
                    item["dn_value"],
                    item["qty_per_pallet"],
                    item["pallet_type"],
                    item["length_mm"],
                    item["width_mm"],
                    item["height_mm"],
                    item["remark"],
                    user.name,
                    user.id,
                    row_id,
                ),
            )
            after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE id=%s", (row_id,)))
            _write_log(
                action="update",
                user=user,
                row_id=row_id,
                material_no=item["material_no"],
                change_summary={"op": "update", "fields": fields},
            )
            updated += 1
            saved.append(after)
            continue
        if _find_dup(item["material_no"], item["pallet_type"]):
            raise HTTPException(
                status_code=400,
                detail=f"该物料号+托盘已存在对照：{item['material_no']} / {item['pallet_type']}",
            )
        new_id = execute(
            f"""
            INSERT INTO {_map_table()} (
                material_no, dn_value, qty_per_pallet, pallet_type,
                length_mm, width_mm, height_mm, remark,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                item["material_no"],
                item["dn_value"],
                item["qty_per_pallet"],
                item["pallet_type"],
                item["length_mm"],
                item["width_mm"],
                item["height_mm"],
                item["remark"],
                user.name,
                user.id,
                user.name,
                user.id,
            ),
        )
        after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE id=%s", (new_id,)))
        _write_log(
            action="create",
            user=user,
            row_id=new_id,
            material_no=item["material_no"],
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


@router.post("/align-dn")
def align_qty_map_dn(user: UserDep):
    return sync_qty_map_dn(user=user, write_notice=True)


@router.delete("/{row_id}")
def delete_qty_map(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM {_map_table()} WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到对照记录")
    before = _row_from_db(current)
    execute(f"DELETE FROM {_map_table()} WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        material_no=before["material_no"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}


DALIAN_CREATE_TABLE_SQL = _create_map_sql(DALIAN_MAP_TABLE, "大连自动装箱-物料数量/托对照", "uk_dl_auto_pack_qty_mat_pallet")
DALIAN_CREATE_LOG_TABLE_SQL = _create_log_sql(DALIAN_LOG_TABLE, "大连自动装箱-物料数量/托操作日志")
