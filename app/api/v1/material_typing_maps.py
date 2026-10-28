"""材质-打字材质对照：独立表与操作日志。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.utils.typing_content import normalize_material_key

router = APIRouter(prefix="/material-typing-maps", tags=["材质-打字材质"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS material_typing_maps (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            material_key VARCHAR(64) NOT NULL COMMENT '材质，如 A105 / 304 / 316',
            typing_material VARCHAR(128) NOT NULL COMMENT '打字材质，如 A105+QT',
            remark TEXT NULL COMMENT '备注说明',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_material_typing_key (material_key),
            INDEX idx_material_typing_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='材质-打字材质对照'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS material_typing_map_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '对照表ID，删除后可空',
            material_key VARCHAR(64) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_material_typing_log_row (row_id),
            INDEX idx_material_typing_log_key (material_key),
            INDEX idx_material_typing_log_action (action),
            INDEX idx_material_typing_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='材质-打字材质对照操作日志'
    """

SELECT_SQL = """
        id, material_key, typing_material, remark,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """

DIFF_KEYS = ("material_key", "typing_material", "remark")

DEFAULT_MAPS = (
    ("A105", "A105+QT", "碳钢"),
    ("304", "A182 F304+AT", "不锈钢"),
    ("316", "A182 F316/F316L+AT", "不锈钢"),
)


class MaterialTypingRowIn(BaseModel):
    id: int | None = None
    material_key: str = ""
    typing_material: str = ""
    remark: str = ""


class MaterialTypingSaveRequest(BaseModel):
    rows: list[MaterialTypingRowIn] = Field(default_factory=list)


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_TABLE_SQL)
    execute(CREATE_LOG_TABLE_SQL)
    _seed_defaults()
    _TABLES_READY = True


def _seed_defaults() -> None:
    """首次建表预置 A105 / 304 / 316，已有数据则不动。"""
    existing = fetch_one("SELECT COUNT(*) AS cnt FROM material_typing_maps")
    if existing and int(existing.get("cnt") or 0) > 0:
        return
    for material_key, typing_material, remark in DEFAULT_MAPS:
        execute(
            """
            INSERT INTO material_typing_maps (
                material_key, typing_material, remark, created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s)
            """,
            (material_key, typing_material, remark, "系统预置", "系统预置"),
        )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": int(row["id"]),
        "material_key": _normalize_str(row.get("material_key")),
        "typing_material": _normalize_str(row.get("typing_material")),
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
        "material_key": row.get("material_key") or "",
        "typing_material": row.get("typing_material") or "",
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
    material_key: str,
    change_summary: dict[str, Any],
) -> None:
    execute(
        """
        INSERT INTO material_typing_map_logs
            (row_id, material_key, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            material_key,
            action,
            user.id,
            user.name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_by_key(material_key: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(
            f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE material_key=%s",
            (material_key,),
        )
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE material_key=%s AND id<>%s",
        (material_key, exclude_id),
    )


def load_typing_material_map() -> dict[str, str]:
    """打字要求生成用：材质键 → 打字材质。"""
    _ensure_tables()
    rows = fetch_all("SELECT material_key, typing_material FROM material_typing_maps")
    out: dict[str, str] = {}
    for row in rows:
        key = normalize_material_key(row.get("material_key"))
        value = _normalize_str(row.get("typing_material"))
        if key and value:
            out[key] = value
    return out


@router.get("")
def list_material_typing_maps(
    _user: UserDep,
    material_key: str = "",
    limit: int = Query(2000, ge=1, le=5000),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = _normalize_str(material_key)
    if query:
        clauses.append("(material_key LIKE %s OR typing_material LIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM material_typing_maps
        WHERE {' AND '.join(clauses)}
        ORDER BY material_key, id
        LIMIT %s
        """,
        (*params, limit),
    )
    data = [_row_from_db(r) for r in rows]
    return {"status": "success", "total": len(data), "rows": data}


@router.get("/logs")
def list_material_typing_map_logs(
    _user: UserDep,
    material_key: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = _normalize_str(material_key)
    if query:
        clauses.append("material_key LIKE %s")
        params.append(f"%{query}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, material_key, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM material_typing_map_logs
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
def save_material_typing_maps(req: MaterialTypingSaveRequest, user: UserDep):
    _ensure_tables()
    prepared: list[tuple[int | None, str, str, str]] = []
    seen: set[str] = set()
    for idx, raw in enumerate(req.rows, start=1):
        material_key = normalize_material_key(raw.material_key)
        typing_material = _normalize_str(raw.typing_material)
        remark = _normalize_str(raw.remark)
        if not material_key and not typing_material and not remark:
            continue
        if not material_key:
            raise HTTPException(status_code=400, detail=f"第{idx}行材质不能为空")
        if not typing_material:
            raise HTTPException(status_code=400, detail=f"第{idx}行打字材质不能为空")
        if material_key in seen:
            raise HTTPException(status_code=400, detail=f"本次保存中材质重复：{material_key}")
        seen.add(material_key)
        prepared.append((int(raw.id) if raw.id else None, material_key, typing_material, remark))

    if not prepared:
        raise HTTPException(status_code=400, detail="没有可保存的行，请至少填写材质与打字材质")

    saved: list[dict[str, Any]] = []
    inserted = 0
    updated = 0
    for row_id, material_key, typing_material, remark in prepared:
        if row_id:
            current = fetch_one(f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE id=%s", (row_id,))
            if not current:
                raise HTTPException(status_code=404, detail=f"未找到对照记录 id={row_id}")
            if _find_by_key(material_key, exclude_id=row_id):
                raise HTTPException(status_code=400, detail=f"该材质已存在对照：{material_key}")
            before = _row_from_db(current)
            after_snap = {
                "material_key": material_key,
                "typing_material": typing_material,
                "remark": remark,
            }
            fields = _diff_fields(_snapshot(before), after_snap)
            if not fields:
                saved.append(before)
                continue
            execute(
                """
                UPDATE material_typing_maps
                SET material_key=%s, typing_material=%s, remark=%s,
                    updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
                WHERE id=%s
                """,
                (material_key, typing_material, remark, user.name, user.id, row_id),
            )
            after = _row_from_db(
                fetch_one(f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE id=%s", (row_id,))
            )
            _write_log(
                action="update",
                user=user,
                row_id=row_id,
                material_key=material_key,
                change_summary={"op": "update", "fields": fields},
            )
            updated += 1
            saved.append(after)
            continue
        if _find_by_key(material_key):
            raise HTTPException(status_code=400, detail=f"该材质已存在对照：{material_key}")
        new_id = execute(
            """
            INSERT INTO material_typing_maps (
                material_key, typing_material, remark,
                created_by, created_by_user_id, updated_by, updated_by_user_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (material_key, typing_material, remark, user.name, user.id, user.name, user.id),
        )
        after = _row_from_db(
            fetch_one(f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE id=%s", (new_id,))
        )
        _write_log(
            action="create",
            user=user,
            row_id=new_id,
            material_key=material_key,
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
def delete_material_typing_map(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM material_typing_maps WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到对照记录")
    before = _row_from_db(current)
    execute("DELETE FROM material_typing_maps WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        material_key=before["material_key"],
        change_summary={"op": "delete", "fields": _snapshot(before)},
    )
    return {"status": "success", "id": row_id}
