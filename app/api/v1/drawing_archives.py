"""图纸建档 / 图纸下载 / 图纸审核 API。"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_drawing_archives_dir
from app.utils.drawing_no import (
    drawing_no_match_variants,
    drawing_rev_for_filename,
    looks_like_drawing_rev,
    normalize_drawing_rev_for_db,
)

router = APIRouter(prefix="/drawing-archives", tags=["图纸建档"])

ALLOWED_EXTENSIONS = {".pdf"}
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TABLES_READY = False

STATUS_UPLOADED = "上传"
STATUS_REVISING = "修改"
STATUS_REVIEWED = "审核"
VALID_STATUSES = {STATUS_UPLOADED, STATUS_REVISING, STATUS_REVIEWED}

DIM_FIELDS = (
    "od_d",
    "id_c",
    "thk_t",
    "thk_f",
    "mouth_m",
    "center_k",
    "seat_a",
    "seat_h_f",
    "r_val",
    "hole_h",
    "hole_n",
    "side_b",
    "side_b_depth",
    "unit_weight",
)
REQUIRED_DIM_FIELDS = ("od_d", "id_c", "thk_t", "unit_weight")


class BatchDownloadRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    order_no: str | None = None


class ReviewRejectRequest(BaseModel):
    remark: str = ""


class DrawingReviseRequest(BaseModel):
    customer_code: str | None = None
    standard_no: str | None = None
    drawing_rev_no: str | None = None
    material: str | None = None
    spec_model: str | None = None
    spec: str | None = None
    model: str | None = None
    material_no: str | None = None
    drawing_type: str | None = None
    od_d: str | None = None
    id_c: str | None = None
    thk_t: str | None = None
    thk_f: str | None = None
    mouth_m: str | None = None
    center_k: str | None = None
    seat_a: str | None = None
    seat_h_f: str | None = None
    r_val: str | None = None
    hole_h: str | None = None
    hole_n: str | None = None
    side_b: str | None = None
    side_b_depth: str | None = None
    unit_weight: str | None = None


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(
        """
        CREATE TABLE IF NOT EXISTS drawing_archives (
            id INT PRIMARY KEY AUTO_INCREMENT,
            customer_code VARCHAR(64) NOT NULL,
            drawing_no VARCHAR(255) NOT NULL,
            material VARCHAR(128) NOT NULL,
            spec_model VARCHAR(255) NOT NULL,
            drawing_type VARCHAR(128) NOT NULL,
            drawing_name VARCHAR(512) NOT NULL,
            storage_relative_path VARCHAR(1024) NOT NULL,
            file_size BIGINT NOT NULL DEFAULT 0,
            content_type VARCHAR(128) NULL,
            uploaded_at DATETIME NOT NULL,
            uploaded_by_user_id INT NULL,
            uploaded_by_name VARCHAR(64) NOT NULL DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_drawing_name (drawing_name),
            INDEX idx_drawing_no (drawing_no),
            INDEX idx_drawing_customer (customer_code),
            INDEX idx_drawing_uploaded_at (uploaded_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS drawing_archive_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            archive_id INT NULL,
            action VARCHAR(32) NOT NULL,
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL,
            INDEX idx_drawing_log_archive (archive_id),
            INDEX idx_drawing_log_action (action),
            INDEX idx_drawing_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    for column_name, stmt in [
        ("standard_no", "ALTER TABLE drawing_archives ADD COLUMN standard_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '标准号' AFTER drawing_no"),
        ("drawing_rev_no", "ALTER TABLE drawing_archives ADD COLUMN drawing_rev_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号-版本号' AFTER standard_no"),
        ("spec", "ALTER TABLE drawing_archives ADD COLUMN spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格' AFTER spec_model"),
        ("model", "ALTER TABLE drawing_archives ADD COLUMN model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号' AFTER spec"),
        ("material_no", "ALTER TABLE drawing_archives ADD COLUMN material_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '物料号' AFTER model"),
        ("od_d", "ALTER TABLE drawing_archives ADD COLUMN od_d VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'D外径' AFTER material_no"),
        ("id_c", "ALTER TABLE drawing_archives ADD COLUMN id_c VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'C内径' AFTER od_d"),
        ("thk_t", "ALTER TABLE drawing_archives ADD COLUMN thk_t VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'T厚度' AFTER id_c"),
        ("thk_f", "ALTER TABLE drawing_archives ADD COLUMN thk_f VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'F总厚度' AFTER thk_t"),
        ("mouth_m", "ALTER TABLE drawing_archives ADD COLUMN mouth_m VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'M上口' AFTER thk_f"),
        ("center_k", "ALTER TABLE drawing_archives ADD COLUMN center_k VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'K中心距' AFTER mouth_m"),
        ("seat_a", "ALTER TABLE drawing_archives ADD COLUMN seat_a VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'A台径' AFTER center_k"),
        ("seat_h_f", "ALTER TABLE drawing_archives ADD COLUMN seat_h_f VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'f台高' AFTER seat_a"),
        ("r_val", "ALTER TABLE drawing_archives ADD COLUMN r_val VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'R' AFTER seat_h_f"),
        ("hole_h", "ALTER TABLE drawing_archives ADD COLUMN hole_h VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'H孔径' AFTER r_val"),
        ("hole_n", "ALTER TABLE drawing_archives ADD COLUMN hole_n VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'N孔数' AFTER hole_h"),
        ("side_b", "ALTER TABLE drawing_archives ADD COLUMN side_b VARCHAR(64) NOT NULL DEFAULT '' COMMENT '侧孔B' AFTER hole_n"),
        ("side_b_depth", "ALTER TABLE drawing_archives ADD COLUMN side_b_depth VARCHAR(64) NOT NULL DEFAULT '' COMMENT '侧孔B深度' AFTER side_b"),
        ("unit_weight", "ALTER TABLE drawing_archives ADD COLUMN unit_weight VARCHAR(64) NOT NULL DEFAULT '' COMMENT '单重' AFTER side_b_depth"),
        ("status", "ALTER TABLE drawing_archives ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT '审核' COMMENT '上传/修改/审核' AFTER unit_weight"),
        ("reviewer_user_id", "ALTER TABLE drawing_archives ADD COLUMN reviewer_user_id INT NULL COMMENT '审核负责人' AFTER status"),
        ("reviewer_name", "ALTER TABLE drawing_archives ADD COLUMN reviewer_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '审核负责人姓名' AFTER reviewer_user_id"),
        ("review_remark", "ALTER TABLE drawing_archives ADD COLUMN review_remark TEXT NULL COMMENT '不一致说明' AFTER reviewer_name"),
        ("reviewed_at", "ALTER TABLE drawing_archives ADD COLUMN reviewed_at DATETIME NULL COMMENT '最近确认时间' AFTER review_remark"),
        ("updated_by_user_id", "ALTER TABLE drawing_archives ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者' AFTER uploaded_by_name"),
        ("updated_by_name", "ALTER TABLE drawing_archives ADD COLUMN updated_by_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '更改者姓名' AFTER updated_by_user_id"),
    ]:
        exists = fetch_one(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = 'drawing_archives' AND column_name = %s
            LIMIT 1
            """,
            (column_name,),
        )
        if not exists:
            execute(stmt)
    for index_name, stmt in [
        ("idx_drawing_status", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_status (status)"),
        ("idx_drawing_reviewer", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_reviewer (reviewer_user_id)"),
        ("idx_drawing_standard_no", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_standard_no (standard_no)"),
        ("idx_drawing_rev_no", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_rev_no (drawing_rev_no)"),
        ("idx_drawing_status_reviewer", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_status_reviewer (status, reviewer_user_id)"),
        ("idx_drawing_status_uploader", "ALTER TABLE drawing_archives ADD INDEX idx_drawing_status_uploader (status, uploaded_by_user_id)"),
    ]:
        exists = fetch_one(
            """
            SELECT 1 FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = 'drawing_archives' AND index_name = %s
            LIMIT 1
            """,
            (index_name,),
        )
        if not exists:
            execute(stmt)
    _backfill_legacy_drawing_keys()
    _TABLES_READY = True


def _backfill_legacy_drawing_keys() -> None:
    rows = fetch_all(
        """
        SELECT id, drawing_no, standard_no, drawing_rev_no
        FROM drawing_archives
        WHERE COALESCE(standard_no, '') = '' AND COALESCE(drawing_rev_no, '') = ''
        """
    )
    for row in rows:
        drawing_no = str(row.get("drawing_no") or "").strip()
        if not drawing_no:
            continue
        if looks_like_drawing_rev(drawing_no):
            execute(
                "UPDATE drawing_archives SET drawing_rev_no=%s WHERE id=%s",
                (normalize_drawing_rev_for_db(drawing_no), row["id"]),
            )
        else:
            execute("UPDATE drawing_archives SET standard_no=%s WHERE id=%s", (drawing_no, row["id"]))
    execute(
        """
        UPDATE drawing_archives
        SET status = %s
        WHERE status IS NULL OR TRIM(status) = ''
        """,
        (STATUS_REVIEWED,),
    )


def _safe_segment(name: str, fallback: str = "unnamed") -> str:
    cleaned = _WIN_INVALID.sub("_", (name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned[:120] or fallback


def _safe_drawing_filename(drawing_name: str) -> str:
    cleaned = _WIN_INVALID.sub("_", (drawing_name or "").strip())
    cleaned = cleaned.rstrip(" .")
    return (cleaned or "drawing")[:200]


def _get_factory_order_no(order_no: str) -> str:
    ono = (order_no or "").strip()
    if not ono:
        return ""
    row = fetch_one(
        """
        SELECT factory_order_no
        FROM order_details
        WHERE order_no = %s
          AND factory_order_no IS NOT NULL
          AND TRIM(factory_order_no) <> ''
        LIMIT 1
        """,
        (ono,),
    )
    if not row or not row.get("factory_order_no"):
        return ""
    return str(row["factory_order_no"]).strip()


def _row_to_dict(row: dict, *, extra: dict | None = None) -> dict:
    def fmt(v: Any) -> Any:
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date):
            return v.strftime("%Y-%m-%d")
        return v

    data = {k: fmt(v) for k, v in row.items()}
    if extra:
        data.update(extra)
    return data


def _write_log(
    archive_id: int | None,
    action: str,
    operator: CurrentUser,
    change_summary: dict | str | None = None,
) -> None:
    summary = change_summary
    if isinstance(summary, dict):
        summary = json.dumps(summary, ensure_ascii=False, default=str)
    execute(
        """
        INSERT INTO drawing_archive_logs
            (archive_id, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (archive_id, action, operator.id, operator.name, summary),
    )


AUDIT_FIELDS = (
    "customer_code",
    "drawing_no",
    "standard_no",
    "drawing_rev_no",
    "material",
    "spec_model",
    "spec",
    "model",
    "material_no",
    "drawing_type",
    "od_d",
    "id_c",
    "thk_t",
    "thk_f",
    "mouth_m",
    "center_k",
    "seat_a",
    "seat_h_f",
    "r_val",
    "hole_h",
    "hole_n",
    "side_b",
    "side_b_depth",
    "unit_weight",
    "drawing_name",
    "storage_relative_path",
    "status",
    "review_remark",
)


def _log_scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).strip()


def _archive_snapshot(row: dict, extra: dict | None = None) -> dict[str, str]:
    data = dict(row)
    if extra:
        data.update(extra)
    return {key: _log_scalar(data.get(key)) for key in AUDIT_FIELDS}


def _archive_field_diff(before: dict, after: dict) -> dict[str, dict[str, str]]:
    changes: dict[str, dict[str, str]] = {}
    for key in AUDIT_FIELDS:
        old = _log_scalar(before.get(key))
        new = _log_scalar(after.get(key))
        if old != new:
            changes[key] = {"from": old, "to": new}
    return changes


def _resolve_identity(standard_no: str, drawing_rev_no: str) -> tuple[str, str, str, str]:
    """返回 (standard_no, drawing_rev_db, match_key, filename_second_seg)。"""
    standard = (standard_no or "").strip()
    rev_raw = (drawing_rev_no or "").strip()
    if bool(standard) == bool(rev_raw):
        raise HTTPException(status_code=400, detail="标准号与图纸号-版本号必须填写其中一个，且不能同时填写")
    if standard:
        return standard, "", standard, standard
    rev_db = normalize_drawing_rev_for_db(rev_raw)
    if not rev_db:
        raise HTTPException(status_code=400, detail="请输入图纸号-版本号")
    return "", rev_db, rev_db, drawing_rev_for_filename(rev_db)


def _resolve_fourth_segment(material_no: str, spec: str, model: str) -> tuple[str, str]:
    mat_no = (material_no or "").strip()
    spec_val = (spec or "").strip()
    model_val = (model or "").strip()
    if mat_no:
        return mat_no, mat_no
    fourth = f"{spec_val} {model_val}".strip()
    if not fourth:
        raise HTTPException(status_code=400, detail="物料号为空时，规格与型号不能同时为空")
    return "", fourth


def _build_drawing_name(
    customer_code: str,
    second: str,
    material: str,
    fourth: str,
    drawing_type: str,
) -> str:
    parts = [customer_code, second, material, fourth, drawing_type]
    for part in parts:
        if not part:
            raise HTTPException(status_code=400, detail="客户代码、标准号或图纸号、材质、物料号或规格型号、类型均为必填")
    return ";".join(parts)


def _require_unique_drawing_name(drawing_name: str, exclude_id: int | None = None) -> None:
    row = fetch_one("SELECT id FROM drawing_archives WHERE drawing_name = %s", (drawing_name,))
    if row and (exclude_id is None or int(row["id"]) != int(exclude_id)):
        raise HTTPException(status_code=400, detail=f"图纸已存在（图纸名：{drawing_name}），不允许覆盖，请删除后重传")


def _absolute_from_relative(rel: str) -> Path:
    root = get_drawing_archives_dir().resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise HTTPException(status_code=400, detail="非法文件路径")
    return target


def _get_archive_or_404(archive_id: int) -> dict:
    row = fetch_one("SELECT * FROM drawing_archives WHERE id = %s", (archive_id,))
    if not row:
        raise HTTPException(status_code=404, detail="图纸归档不存在")
    return row


def _require_dim_fields(values: dict[str, str]) -> None:
    labels = {
        "spec_model": "规格型号",
        "od_d": "D外径",
        "id_c": "C内径",
        "thk_t": "T厚度",
        "unit_weight": "单重",
    }
    missing = [labels[k] for k in ("spec_model", *REQUIRED_DIM_FIELDS) if not (values.get(k) or "").strip()]
    if missing:
        raise HTTPException(status_code=400, detail=f"请填写必填项：{'、'.join(missing)}")


def _parse_unit_weight(raw: str) -> float:
    text = (raw or "").strip().replace(",", "")
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _get_reviewer(user_id: int) -> dict:
    row = fetch_one("SELECT id, name, username FROM users WHERE id = %s", (user_id,))
    if not row:
        raise HTTPException(status_code=400, detail="审核负责人不存在")
    return row


def _last_submit_at(archive_id: int, fallback: Any) -> Any:
    row = fetch_one(
        """
        SELECT MAX(operated_at) AS t
        FROM drawing_archive_logs
        WHERE archive_id = %s AND action IN ('upload', 'resubmit', 'update')
        """,
        (archive_id,),
    )
    return (row.get("t") if row else None) or fallback


def _reviewer_has_downloaded(row: dict) -> bool:
    reviewer_id = row.get("reviewer_user_id")
    if not reviewer_id:
        return False
    since = _last_submit_at(int(row["id"]), row.get("uploaded_at"))
    found = fetch_one(
        """
        SELECT id FROM drawing_archive_logs
        WHERE archive_id = %s AND action = 'download' AND operator_user_id = %s
          AND operated_at >= %s
        LIMIT 1
        """,
        (row["id"], reviewer_id, since),
    )
    return bool(found)


def _reviewer_downloaded_map(rows: list[dict]) -> dict[int, bool]:
    result = {int(row["id"]): False for row in rows}
    ids = [int(row["id"]) for row in rows if row.get("reviewer_user_id")]
    if not ids:
        return result
    placeholders = ", ".join(["%s"] * len(ids))
    submits = fetch_all(
        f"""
        SELECT archive_id, MAX(operated_at) AS t
        FROM drawing_archive_logs
        WHERE archive_id IN ({placeholders}) AND action IN ('upload', 'resubmit', 'update')
        GROUP BY archive_id
        """,
        tuple(ids),
    )
    submit_map = {int(row["archive_id"]): row.get("t") for row in submits}
    downloads = fetch_all(
        f"""
        SELECT archive_id, operator_user_id, MAX(operated_at) AS last_download
        FROM drawing_archive_logs
        WHERE archive_id IN ({placeholders}) AND action = 'download'
        GROUP BY archive_id, operator_user_id
        """,
        tuple(ids),
    )
    download_map: dict[tuple[int, int], Any] = {
        (int(row["archive_id"]), int(row["operator_user_id"])): row.get("last_download")
        for row in downloads
        if row.get("operator_user_id") is not None
    }
    for row in rows:
        aid = int(row["id"])
        reviewer_id = row.get("reviewer_user_id")
        if not reviewer_id:
            continue
        since = submit_map.get(aid) or row.get("uploaded_at")
        last = download_map.get((aid, int(reviewer_id)))
        result[aid] = bool(last and since and last >= since)
    return result


def get_pending_review_items(user_id: int) -> list[dict]:
    _ensure_tables()
    rows = fetch_all(
        """
        SELECT * FROM drawing_archives
        WHERE status = %s AND reviewer_user_id = %s
        ORDER BY uploaded_at DESC, id DESC
        """,
        (STATUS_UPLOADED, user_id),
    )
    downloaded = _reviewer_downloaded_map(rows)
    return [
        _row_to_dict(row, extra={"downloaded_after_submit": downloaded.get(int(row["id"]), False)})
        for row in rows
    ]


def get_pending_revise_items(user_id: int) -> list[dict]:
    _ensure_tables()
    rows = fetch_all(
        """
        SELECT * FROM drawing_archives
        WHERE status = %s AND uploaded_by_user_id = %s
        ORDER BY updated_at DESC, id DESC
        """,
        (STATUS_REVISING, user_id),
    )
    return [_row_to_dict(r) for r in rows]


def _match_key_sql(variants: list[str]) -> tuple[str, tuple[Any, ...]]:
    placeholders = ", ".join(["%s"] * len(variants))
    clause = (
        f"(drawing_no IN ({placeholders}) OR standard_no IN ({placeholders}) OR drawing_rev_no IN ({placeholders}))"
    )
    return clause, tuple(variants) * 3


def _approved_by_drawing_keys(keys: list[str]) -> list[dict]:
    variants: list[str] = []
    for key in keys:
        for item in drawing_no_match_variants(key):
            if item not in variants:
                variants.append(item)
    if not variants:
        return []
    clause, params = _match_key_sql(variants)
    return fetch_all(
        f"""
        SELECT * FROM drawing_archives
        WHERE status = %s AND {clause}
        ORDER BY drawing_name ASC, id ASC
        """,
        (STATUS_REVIEWED, *params),
    )


def _target_rel_path(customer_code: str, drawing_name: str) -> str:
    folder = _safe_segment(customer_code)
    file_stem = _safe_drawing_filename(drawing_name)
    return f"{folder}/{file_stem}.pdf".replace("\\", "/")


def _apply_storage_update(
    row: dict,
    customer_code: str,
    drawing_name: str,
    new_raw: bytes | None,
    content_type: str | None,
) -> tuple[str, str, int, bool, Path | None, Path | None]:
    """返回 (rel_path, content_type, file_size, replaced_file, old_path_to_delete, new_path_rollback)."""
    old_rel = str(row["storage_relative_path"] or "").replace("\\", "/")
    old_path = _absolute_from_relative(old_rel)
    new_rel = _target_rel_path(customer_code, drawing_name)
    new_path = _absolute_from_relative(new_rel)
    same_path = old_rel == new_rel
    old_size = int(row.get("file_size") or 0)
    old_ct = str(row.get("content_type") or "application/pdf")

    if new_raw is not None:
        if not same_path and new_path.exists():
            raise HTTPException(status_code=400, detail=f"磁盘上已存在同名文件：{new_path.name}")
        new_path.parent.mkdir(parents=True, exist_ok=True)
        new_path.write_bytes(new_raw)
        ct = content_type or "application/pdf"
        rollback = None if same_path else new_path
        delete_old = None if same_path else old_path
        return new_rel, ct, len(new_raw), True, delete_old, rollback

    if same_path:
        return old_rel, old_ct, old_size, False, None, None
    if not old_path.is_file():
        raise HTTPException(status_code=404, detail="图纸文件不存在或已被移动，请重新上传 PDF")
    if new_path.exists():
        raise HTTPException(status_code=400, detail=f"磁盘上已存在同名文件：{new_path.name}")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_bytes(old_path.read_bytes())
    return new_rel, old_ct, old_size, False, old_path, new_path


def _save_pdf(customer_code: str, drawing_name: str, upload: UploadFile, raw: bytes) -> tuple[str, str]:
    original_name = Path(upload.filename or "drawing.pdf").name
    ext = Path(original_name).suffix.lower()
    if ext != ".pdf":
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件（.pdf）")
    if not raw:
        raise HTTPException(status_code=400, detail="文件不能为空")

    folder = _safe_segment(customer_code)
    file_stem = _safe_drawing_filename(drawing_name)
    rel_path = f"{folder}/{file_stem}.pdf"
    abs_path = get_drawing_archives_dir() / folder / f"{file_stem}.pdf"
    if abs_path.exists():
        raise HTTPException(status_code=400, detail=f"磁盘上已存在同名文件：{file_stem}.pdf")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(raw)
    content_type = upload.content_type or "application/pdf"
    return rel_path.replace("\\", "/"), content_type


def _collect_payload(
    *,
    customer_code: str,
    standard_no: str,
    drawing_rev_no: str,
    material: str,
    spec_model: str,
    spec: str,
    model: str,
    material_no: str,
    drawing_type: str,
    dims: dict[str, str],
) -> dict[str, str]:
    customer = customer_code.strip()
    mat = material.strip()
    dtype = drawing_type.strip()
    spec_model_val = spec_model.strip()
    spec_val = spec.strip()
    model_val = model.strip()
    if not customer or not mat or not dtype:
        raise HTTPException(status_code=400, detail="客户代码、材质、类型均为必填")
    _require_dim_fields({"spec_model": spec_model_val, **dims})
    standard, rev_db, match_key, name_second = _resolve_identity(standard_no, drawing_rev_no)
    mat_no, fourth = _resolve_fourth_segment(material_no, spec_val, model_val)
    return {
        "customer_code": customer,
        "standard_no": standard,
        "drawing_rev_no": rev_db,
        "drawing_no": match_key,
        "material": mat,
        "spec_model": spec_model_val,
        "spec": spec_val,
        "model": model_val,
        "material_no": mat_no,
        "drawing_type": dtype,
        "name_second": name_second,
        "name_fourth": fourth,
        **{k: (dims.get(k) or "").strip() for k in DIM_FIELDS},
    }


def _upsert_material(payload: dict[str, str], operator: str) -> str:
    from app.api.v1.workflow import upsert_material_from_drawing

    material_no = payload["material_no"] or payload["name_fourth"]
    drawing_no = payload["drawing_rev_no"] if payload["drawing_rev_no"] else "-"
    return upsert_material_from_drawing(
        material_no=material_no,
        drawing_no=drawing_no,
        standard_no=payload["standard_no"],
        spec=payload["spec"],
        model=payload["model"],
        spec_model=payload["spec_model"],
        material=payload["material"],
        unit_weight=_parse_unit_weight(payload["unit_weight"]),
        operator=operator,
    )


@router.post("")
async def upload_drawing(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_code: str = Form(...),
    material: str = Form(...),
    drawing_type: str = Form(...),
    spec_model: str = Form(...),
    spec: str = Form(""),
    model: str = Form(""),
    material_no: str = Form(""),
    standard_no: str = Form(""),
    drawing_rev_no: str = Form(""),
    od_d: str = Form(...),
    id_c: str = Form(...),
    thk_t: str = Form(...),
    thk_f: str = Form(""),
    mouth_m: str = Form(""),
    center_k: str = Form(""),
    seat_a: str = Form(""),
    seat_h_f: str = Form(""),
    r_val: str = Form(""),
    hole_h: str = Form(""),
    hole_n: str = Form(""),
    side_b: str = Form(""),
    side_b_depth: str = Form(""),
    unit_weight: str = Form(...),
    reviewer_user_id: int = Form(...),
    file: UploadFile = File(...),
):
    _ensure_tables()
    dims = {
        "od_d": od_d,
        "id_c": id_c,
        "thk_t": thk_t,
        "thk_f": thk_f,
        "mouth_m": mouth_m,
        "center_k": center_k,
        "seat_a": seat_a,
        "seat_h_f": seat_h_f,
        "r_val": r_val,
        "hole_h": hole_h,
        "hole_n": hole_n,
        "side_b": side_b,
        "side_b_depth": side_b_depth,
        "unit_weight": unit_weight,
    }
    payload = _collect_payload(
        customer_code=customer_code,
        standard_no=standard_no,
        drawing_rev_no=drawing_rev_no,
        material=material,
        spec_model=spec_model,
        spec=spec,
        model=model,
        material_no=material_no,
        drawing_type=drawing_type,
        dims=dims,
    )
    reviewer = _get_reviewer(int(reviewer_user_id))
    drawing_name = _build_drawing_name(
        payload["customer_code"],
        payload["name_second"],
        payload["material"],
        payload["name_fourth"],
        payload["drawing_type"],
    )
    _require_unique_drawing_name(drawing_name)

    raw = await file.read()
    rel_path, content_type = _save_pdf(payload["customer_code"], drawing_name, file, raw)
    uploaded_at = datetime.now()

    try:
        new_id = execute(
            """
            INSERT INTO drawing_archives (
                customer_code, drawing_no, standard_no, drawing_rev_no, material,
                spec_model, spec, model, material_no, drawing_type,
                od_d, id_c, thk_t, thk_f, mouth_m, center_k, seat_a, seat_h_f,
                r_val, hole_h, hole_n, side_b, side_b_depth, unit_weight,
                drawing_name, storage_relative_path, file_size, content_type,
                uploaded_at, uploaded_by_user_id, uploaded_by_name,
                updated_by_user_id, updated_by_name,
                status, reviewer_user_id, reviewer_name, review_remark, reviewed_at
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                payload["customer_code"],
                payload["drawing_no"],
                payload["standard_no"],
                payload["drawing_rev_no"],
                payload["material"],
                payload["spec_model"],
                payload["spec"],
                payload["model"],
                payload["material_no"],
                payload["drawing_type"],
                payload["od_d"],
                payload["id_c"],
                payload["thk_t"],
                payload["thk_f"],
                payload["mouth_m"],
                payload["center_k"],
                payload["seat_a"],
                payload["seat_h_f"],
                payload["r_val"],
                payload["hole_h"],
                payload["hole_n"],
                payload["side_b"],
                payload["side_b_depth"],
                payload["unit_weight"],
                drawing_name,
                rel_path,
                len(raw),
                content_type,
                uploaded_at,
                user.id,
                user.name,
                user.id,
                user.name,
                STATUS_UPLOADED,
                int(reviewer["id"]),
                str(reviewer.get("name") or ""),
                None,
                None,
            ),
        )
    except Exception:
        abs_path = _absolute_from_relative(rel_path)
        if abs_path.is_file():
            abs_path.unlink(missing_ok=True)
        raise

    material_action = _upsert_material(payload, user.name)
    row = _get_archive_or_404(new_id)
    _write_log(
        new_id,
        "upload",
        user,
        {
            "fields": _archive_snapshot(row),
            "reviewer_user_id": int(reviewer["id"]),
            "reviewer_name": str(reviewer.get("name") or ""),
            "material_action": material_action,
            "path": rel_path,
            "file_size": len(raw),
        },
    )
    return {
        "item": _row_to_dict(row),
        "material_action": material_action,
        "cleared_pending_docs": 0,
    }


@router.get("")
def list_drawings(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_code: str | None = Query(None),
    drawing_no: str | None = Query(None),
    standard_no: str | None = Query(None),
    drawing_rev_no: str | None = Query(None),
    material: str | None = Query(None),
    spec_model: str | None = Query(None),
    spec: str | None = Query(None),
    model: str | None = Query(None),
    material_no: str | None = Query(None),
    drawing_type: str | None = Query(None),
    drawing_name: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    _ensure_tables()
    filters = {
        "customer_code": (customer_code or "").strip(),
        "standard_no": (standard_no or "").strip(),
        "drawing_rev_no": (drawing_rev_no or "").strip(),
        "material": (material or "").strip(),
        "spec_model": (spec_model or "").strip(),
        "spec": (spec or "").strip(),
        "model": (model or "").strip(),
        "material_no": (material_no or "").strip(),
        "drawing_type": (drawing_type or "").strip(),
        "drawing_name": (drawing_name or "").strip(),
        "status": (status or "").strip(),
    }
    drawing_no_q = (drawing_no or "").strip()
    if not any(filters.values()) and not drawing_no_q:
        raise HTTPException(status_code=400, detail="请至少输入一个查询条件")

    clauses: list[str] = []
    params: list[Any] = []
    for col, val in filters.items():
        if val:
            clauses.append(f"{col} LIKE %s")
            params.append(f"%{val}%")
    if drawing_no_q:
        clauses.append("(drawing_no LIKE %s OR standard_no LIKE %s OR drawing_rev_no LIKE %s)")
        params.extend([f"%{drawing_no_q}%"] * 3)
    where = " AND ".join(clauses)
    params.append(limit)
    rows = fetch_all(
        f"""
        SELECT * FROM drawing_archives
        WHERE {where}
        ORDER BY uploaded_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return {"items": [_row_to_dict(r) for r in rows], "total": len(rows)}


@router.get("/pending-review")
def pending_review(user: Annotated[CurrentUser, Depends(get_current_user)]):
    items = get_pending_review_items(user.id)
    return {"items": items, "total": len(items)}


@router.get("/pending-revise")
def pending_revise(user: Annotated[CurrentUser, Depends(get_current_user)]):
    items = get_pending_revise_items(user.id)
    return {"items": items, "total": len(items)}


@router.get("/by-drawing-name")
def get_by_drawing_name(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    drawing_name: str = Query(...),
):
    _ensure_tables()
    name = drawing_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请输入图纸名")
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    row = fetch_one(
        "SELECT * FROM drawing_archives WHERE drawing_name = %s AND status = %s",
        (name, STATUS_REVIEWED),
    )
    items = [_row_to_dict(row)] if row else []
    return {"items": items, "total": len(items)}


@router.get("/by-order")
def get_by_order(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    order_no: str = Query(...),
):
    _ensure_tables()
    ono = order_no.strip()
    if not ono:
        raise HTTPException(status_code=400, detail="请输入订单号")

    detail_rows = fetch_all(
        """
        SELECT DISTINCT drawing_no
        FROM order_details
        WHERE order_no = %s
          AND drawing_no IS NOT NULL
          AND TRIM(drawing_no) <> ''
        """,
        (ono,),
    )
    drawing_nos = [str(r["drawing_no"]).strip() for r in detail_rows if r.get("drawing_no")]
    factory_order_no = _get_factory_order_no(ono)
    if not drawing_nos:
        return {
            "items": [],
            "total": 0,
            "factory_order_no": factory_order_no,
            "message": "该订单不存在或无图纸号",
        }

    rows = _approved_by_drawing_keys(drawing_nos)
    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": len(rows),
        "drawing_nos": drawing_nos,
        "factory_order_no": factory_order_no,
        "message": None if rows else "订单有图纸号，但无已审核的匹配图纸",
    }


@router.post("/batch-download")
def batch_download(
    body: BatchDownloadRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    ids = [int(x) for x in body.ids if int(x) > 0]
    if not ids:
        raise HTTPException(status_code=400, detail="请选择要下载的图纸")

    placeholders = ", ".join(["%s"] * len(ids))
    rows = fetch_all(
        f"SELECT * FROM drawing_archives WHERE id IN ({placeholders}) AND status = %s ORDER BY id ASC",
        (*ids, STATUS_REVIEWED),
    )
    if len(rows) != len(set(ids)):
        raise HTTPException(status_code=404, detail="部分图纸不存在或尚未审核，请刷新后重试")

    buf = io.BytesIO()
    missing: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            path = _absolute_from_relative(str(row["storage_relative_path"]))
            arcname = f"{row['drawing_name']}.pdf"
            if not path.is_file():
                missing.append(str(row["drawing_name"]))
                continue
            zf.write(path, arcname=arcname)
    if missing and len(missing) == len(rows):
        raise HTTPException(status_code=404, detail="所选图纸文件均不存在或已被移动")

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    order_no = (body.order_no or "").strip()
    factory_order_no = _get_factory_order_no(order_no) if order_no else ""
    order_seg = _safe_segment(order_no, fallback="") if order_no else ""
    factory_seg = _safe_segment(factory_order_no, fallback="") if factory_order_no else ""
    zip_name = f"【DR】{order_seg}_{factory_seg}_{stamp}.zip"
    _write_log(
        None,
        "batch_download",
        user,
        {
            "ids": [int(r["id"]) for r in rows],
            "drawing_names": [str(r["drawing_name"]) for r in rows],
            "missing_files": missing,
            "order_no": order_no or None,
            "factory_order_no": factory_order_no or None,
            "zip_name": zip_name,
        },
    )
    buf.seek(0)
    ascii_name = zip_name.encode("ascii", "ignore").decode("ascii") or f"DR_{stamp}.zip"
    headers = {
        "Content-Disposition": (
            f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(zip_name)}"
        ),
    }
    return Response(content=buf.getvalue(), media_type="application/zip", headers=headers)


def _apply_revise_fields(row: dict, body: DrawingReviseRequest) -> dict[str, str]:
    def pick(name: str, current: str) -> str:
        value = getattr(body, name)
        return current if value is None else str(value).strip()

    dims = {k: pick(k, str(row.get(k) or "")) for k in DIM_FIELDS}
    return _collect_payload(
        customer_code=pick("customer_code", str(row.get("customer_code") or "")),
        standard_no=pick("standard_no", str(row.get("standard_no") or "")),
        drawing_rev_no=pick("drawing_rev_no", str(row.get("drawing_rev_no") or "")),
        material=pick("material", str(row.get("material") or "")),
        spec_model=pick("spec_model", str(row.get("spec_model") or "")),
        spec=pick("spec", str(row.get("spec") or "")),
        model=pick("model", str(row.get("model") or "")),
        material_no=pick("material_no", str(row.get("material_no") or "")),
        drawing_type=pick("drawing_type", str(row.get("drawing_type") or "")),
        dims=dims,
    )


def _update_archive_fields(archive_id: int, payload: dict[str, str], operator: CurrentUser) -> None:
    execute(
        """
        UPDATE drawing_archives
        SET customer_code=%s, drawing_no=%s, standard_no=%s, drawing_rev_no=%s, material=%s,
            spec_model=%s, spec=%s, model=%s, material_no=%s, drawing_type=%s,
            od_d=%s, id_c=%s, thk_t=%s, thk_f=%s, mouth_m=%s, center_k=%s, seat_a=%s, seat_h_f=%s,
            r_val=%s, hole_h=%s, hole_n=%s, side_b=%s, side_b_depth=%s, unit_weight=%s,
            updated_by_user_id=%s, updated_by_name=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (
            payload["customer_code"],
            payload["drawing_no"],
            payload["standard_no"],
            payload["drawing_rev_no"],
            payload["material"],
            payload["spec_model"],
            payload["spec"],
            payload["model"],
            payload["material_no"],
            payload["drawing_type"],
            payload["od_d"],
            payload["id_c"],
            payload["thk_t"],
            payload["thk_f"],
            payload["mouth_m"],
            payload["center_k"],
            payload["seat_a"],
            payload["seat_h_f"],
            payload["r_val"],
            payload["hole_h"],
            payload["hole_n"],
            payload["side_b"],
            payload["side_b_depth"],
            payload["unit_weight"],
            operator.id,
            operator.name,
            archive_id,
        ),
    )


@router.get("/{archive_id}")
def get_drawing(archive_id: int, user: Annotated[CurrentUser, Depends(get_current_user)]):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    extra = {}
    if str(row.get("status") or "") == STATUS_UPLOADED:
        extra["downloaded_after_submit"] = _reviewer_has_downloaded(row)
    return {"item": _row_to_dict(row, extra=extra)}


@router.get("/{archive_id}/download")
def download_drawing(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    path = _absolute_from_relative(str(row["storage_relative_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图纸文件不存在或已被移动")
    _write_log(
        archive_id,
        "download",
        user,
        {"drawing_name": row["drawing_name"], "path": row["storage_relative_path"]},
    )
    filename = f"{row['drawing_name']}.pdf"
    return FileResponse(
        path=path,
        filename=filename,
        media_type=row.get("content_type") or "application/pdf",
        content_disposition_type="attachment",
    )


@router.post("/{archive_id}/review-confirm")
def review_confirm(archive_id: int, user: Annotated[CurrentUser, Depends(get_current_user)]):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    if str(row.get("status") or "") != STATUS_UPLOADED:
        raise HTTPException(status_code=400, detail="仅「上传」状态的图纸可由审核人确认")
    if int(row.get("reviewer_user_id") or 0) != int(user.id):
        raise HTTPException(status_code=403, detail="仅指定的审核负责人可以确认")
    if not _reviewer_has_downloaded(row):
        raise HTTPException(status_code=400, detail="请先下载该图纸并核对录入内容")
    now = datetime.now()
    execute(
        """
        UPDATE drawing_archives
        SET status=%s, review_remark=NULL, reviewed_at=%s,
            updated_by_user_id=%s, updated_by_name=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (STATUS_REVIEWED, now, user.id, user.name, archive_id),
    )
    _write_log(
        archive_id,
        "review_confirm",
        user,
        {"fields": {"status": {"from": STATUS_UPLOADED, "to": STATUS_REVIEWED}}},
    )
    from app.api.v1.workflow import clear_pending_doc_status_for_drawing_no

    cleared = clear_pending_doc_status_for_drawing_no(str(row.get("drawing_no") or ""))
    return {"item": _row_to_dict(_get_archive_or_404(archive_id)), "cleared_pending_docs": cleared}


@router.post("/{archive_id}/review-reject")
def review_reject(
    archive_id: int,
    body: ReviewRejectRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    if str(row.get("status") or "") != STATUS_UPLOADED:
        raise HTTPException(status_code=400, detail="仅「上传」状态的图纸可标记不一致")
    if int(row.get("reviewer_user_id") or 0) != int(user.id):
        raise HTTPException(status_code=403, detail="仅指定的审核负责人可以操作")
    remark = (body.remark or "").strip()
    execute(
        """
        UPDATE drawing_archives
        SET status=%s, review_remark=%s,
            updated_by_user_id=%s, updated_by_name=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (STATUS_REVISING, remark or None, user.id, user.name, archive_id),
    )
    _write_log(
        archive_id,
        "review_reject",
        user,
        {
            "fields": {
                "status": {"from": STATUS_UPLOADED, "to": STATUS_REVISING},
                "review_remark": {"from": _log_scalar(row.get("review_remark")), "to": remark},
            },
            "remark": remark,
        },
    )
    return {"item": _row_to_dict(_get_archive_or_404(archive_id))}


@router.post("/{archive_id}/update")
async def update_drawing(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    customer_code: str = Form(...),
    material: str = Form(...),
    drawing_type: str = Form(...),
    spec_model: str = Form(...),
    spec: str = Form(""),
    model: str = Form(""),
    material_no: str = Form(""),
    standard_no: str = Form(""),
    drawing_rev_no: str = Form(""),
    od_d: str = Form(...),
    id_c: str = Form(...),
    thk_t: str = Form(...),
    thk_f: str = Form(""),
    mouth_m: str = Form(""),
    center_k: str = Form(""),
    seat_a: str = Form(""),
    seat_h_f: str = Form(""),
    r_val: str = Form(""),
    hole_h: str = Form(""),
    hole_n: str = Form(""),
    side_b: str = Form(""),
    side_b_depth: str = Form(""),
    unit_weight: str = Form(...),
    file: UploadFile | None = File(default=None),
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    before = _archive_snapshot(row)
    old_status = str(row.get("status") or "")
    old_drawing_no = str(row.get("drawing_no") or "").strip()
    dims = {
        "od_d": od_d,
        "id_c": id_c,
        "thk_t": thk_t,
        "thk_f": thk_f,
        "mouth_m": mouth_m,
        "center_k": center_k,
        "seat_a": seat_a,
        "seat_h_f": seat_h_f,
        "r_val": r_val,
        "hole_h": hole_h,
        "hole_n": hole_n,
        "side_b": side_b,
        "side_b_depth": side_b_depth,
        "unit_weight": unit_weight,
    }
    payload = _collect_payload(
        customer_code=customer_code,
        standard_no=standard_no,
        drawing_rev_no=drawing_rev_no,
        material=material,
        spec_model=spec_model,
        spec=spec,
        model=model,
        material_no=material_no,
        drawing_type=drawing_type,
        dims=dims,
    )
    drawing_name = _build_drawing_name(
        payload["customer_code"],
        payload["name_second"],
        payload["material"],
        payload["name_fourth"],
        payload["drawing_type"],
    )
    _require_unique_drawing_name(drawing_name, exclude_id=archive_id)

    new_raw: bytes | None = None
    new_ct: str | None = None
    if file is not None and (file.filename or "").strip():
        original_name = Path(file.filename or "drawing.pdf").name
        if Path(original_name).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="仅支持 PDF 文件（.pdf）")
        new_raw = await file.read()
        if not new_raw:
            raise HTTPException(status_code=400, detail="文件不能为空")
        new_ct = file.content_type or "application/pdf"

    rel_path, content_type, file_size, replaced_file, delete_old, rollback_new = _apply_storage_update(
        row,
        payload["customer_code"],
        drawing_name,
        new_raw,
        new_ct,
    )
    try:
        execute(
            """
            UPDATE drawing_archives
            SET customer_code=%s, drawing_no=%s, standard_no=%s, drawing_rev_no=%s, material=%s,
                spec_model=%s, spec=%s, model=%s, material_no=%s, drawing_type=%s,
                od_d=%s, id_c=%s, thk_t=%s, thk_f=%s, mouth_m=%s, center_k=%s, seat_a=%s, seat_h_f=%s,
                r_val=%s, hole_h=%s, hole_n=%s, side_b=%s, side_b_depth=%s, unit_weight=%s,
                drawing_name=%s, storage_relative_path=%s, file_size=%s, content_type=%s,
                status=%s, review_remark=NULL, reviewed_at=NULL,
                updated_by_user_id=%s, updated_by_name=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (
                payload["customer_code"],
                payload["drawing_no"],
                payload["standard_no"],
                payload["drawing_rev_no"],
                payload["material"],
                payload["spec_model"],
                payload["spec"],
                payload["model"],
                payload["material_no"],
                payload["drawing_type"],
                payload["od_d"],
                payload["id_c"],
                payload["thk_t"],
                payload["thk_f"],
                payload["mouth_m"],
                payload["center_k"],
                payload["seat_a"],
                payload["seat_h_f"],
                payload["r_val"],
                payload["hole_h"],
                payload["hole_n"],
                payload["side_b"],
                payload["side_b_depth"],
                payload["unit_weight"],
                drawing_name,
                rel_path,
                file_size,
                content_type,
                STATUS_UPLOADED,
                user.id,
                user.name,
                archive_id,
            ),
        )
    except Exception:
        if rollback_new is not None and rollback_new.is_file():
            rollback_new.unlink(missing_ok=True)
        raise

    if delete_old is not None and delete_old.is_file():
        delete_old.unlink(missing_ok=True)

    material_action = _upsert_material(payload, user.name)
    marked = 0
    if old_status == STATUS_REVIEWED:
        from app.api.v1.workflow import mark_pending_doc_status_for_drawing_no

        marked = mark_pending_doc_status_for_drawing_no(old_drawing_no)
    after_row = _get_archive_or_404(archive_id)
    _write_log(
        archive_id,
        "update",
        user,
        {
            "fields": _archive_field_diff(before, _archive_snapshot(after_row)),
            "replaced_file": replaced_file,
            "path": rel_path,
            "material_action": material_action,
            "re_marked_pending_docs": marked,
        },
    )
    return {
        "item": _row_to_dict(after_row),
        "material_action": material_action,
        "re_marked_pending_docs": marked,
    }


@router.patch("/{archive_id}")
def revise_drawing(
    archive_id: int,
    body: DrawingReviseRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    if str(row.get("status") or "") != STATUS_REVISING:
        raise HTTPException(status_code=400, detail="仅「修改」状态可由上传者改数据")
    if int(row.get("uploaded_by_user_id") or 0) != int(user.id):
        raise HTTPException(status_code=403, detail="仅上传者可以修改该图纸数据")
    payload = _apply_revise_fields(row, body)
    _update_archive_fields(archive_id, payload, user)
    material_action = _upsert_material(payload, user.name)
    after_row = _get_archive_or_404(archive_id)
    _write_log(
        archive_id,
        "revise",
        user,
        {
            "fields": _archive_field_diff(_archive_snapshot(row), _archive_snapshot(after_row)),
            "material_action": material_action,
        },
    )
    return {"item": _row_to_dict(after_row), "material_action": material_action}


@router.post("/{archive_id}/resubmit")
def resubmit_drawing(
    archive_id: int,
    body: DrawingReviseRequest,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    if str(row.get("status") or "") != STATUS_REVISING:
        raise HTTPException(status_code=400, detail="仅「修改」状态可由上传者重新提交")
    if int(row.get("uploaded_by_user_id") or 0) != int(user.id):
        raise HTTPException(status_code=403, detail="仅上传者可以重新提交")
    payload = _apply_revise_fields(row, body)
    _update_archive_fields(archive_id, payload, user)
    material_action = _upsert_material(payload, user.name)
    execute(
        """
        UPDATE drawing_archives
        SET status=%s, review_remark=NULL,
            updated_by_user_id=%s, updated_by_name=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (STATUS_UPLOADED, user.id, user.name, archive_id),
    )
    after_row = _get_archive_or_404(archive_id)
    _write_log(
        archive_id,
        "resubmit",
        user,
        {
            "fields": _archive_field_diff(_archive_snapshot(row), _archive_snapshot(after_row)),
            "material_action": material_action,
        },
    )
    return {"item": _row_to_dict(after_row), "material_action": material_action}


@router.delete("/{archive_id}")
def delete_drawing(
    archive_id: int,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    _ensure_tables()
    row = _get_archive_or_404(archive_id)
    rel = str(row["storage_relative_path"])
    path = _absolute_from_relative(rel)
    file_deleted = False
    file_missing = False
    if path.is_file():
        path.unlink()
        file_deleted = True
    else:
        file_missing = True

    drawing_no = str(row.get("drawing_no") or "").strip()
    was_reviewed = str(row.get("status") or "") == STATUS_REVIEWED
    execute("DELETE FROM drawing_archives WHERE id = %s", (archive_id,))
    _write_log(
        archive_id,
        "delete",
        user,
        {
            "fields": _archive_snapshot(row),
            "drawing_name": row["drawing_name"],
            "path": rel,
            "file_deleted": file_deleted,
            "file_missing_before_delete": file_missing,
            "customer_code": row["customer_code"],
            "drawing_no": row["drawing_no"],
            "status": row.get("status"),
        },
    )
    marked = 0
    if was_reviewed:
        from app.api.v1.workflow import mark_pending_doc_status_for_drawing_no

        marked = mark_pending_doc_status_for_drawing_no(drawing_no)
    return {"ok": True, "file_missing_before_delete": file_missing, "re_marked_pending_docs": marked}
