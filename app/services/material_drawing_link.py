"""物料表与图纸建档的稳定对应：drawing_archive_id。"""
from __future__ import annotations

from typing import Any

from app.core.database import execute, fetch_all, fetch_one
from app.utils.drawing_no import drawing_no_in_sql


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def ensure_material_drawing_archive_column() -> None:
    row = fetch_one(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'materials'
          AND column_name = 'drawing_archive_id'
        LIMIT 1
        """
    )
    if row:
        return
    execute(
        """
        ALTER TABLE materials
        ADD COLUMN drawing_archive_id BIGINT NULL COMMENT '对应图纸建档 id'
        AFTER drawing_no
        """
    )
    idx = fetch_one(
        """
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = 'materials'
          AND index_name = 'idx_material_drawing_archive'
        LIMIT 1
        """
    )
    if not idx:
        execute("ALTER TABLE materials ADD INDEX idx_material_drawing_archive (drawing_archive_id)")


def _drawing_candidates(*, material_no: str, drawing_no: str) -> list[dict[str, Any]]:
    key_no = _norm(material_no)
    key_drawing = _norm(drawing_no)
    if not key_drawing:
        return []
    drawing_sql, drawing_params = drawing_no_in_sql("TRIM(COALESCE(drawing_no, ''))", key_drawing)
    rev_sql, rev_params = drawing_no_in_sql("TRIM(COALESCE(drawing_rev_no, ''))", key_drawing)
    clauses = [f"({drawing_sql} OR {rev_sql})"]
    params: list[Any] = [*drawing_params, *rev_params]
    if key_no:
        clauses.append("TRIM(COALESCE(material_no, ''))=%s")
        params.append(key_no)
    return fetch_all(
        f"""
        SELECT id, material_no, drawing_no, drawing_rev_no, standard_no, spec, model, spec_model,
               material, unit_weight, od_d, mouth_n, thk_t, thk_f, seat_h_f, id_c, status, updated_at
        FROM drawing_archives
        WHERE {' AND '.join(clauses)}
        ORDER BY (CASE WHEN status='审核' THEN 0 ELSE 1 END), updated_at DESC, id DESC
        """,
        tuple(params),
    )


def pick_unique_drawing(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    reviewed = [row for row in candidates if _norm(row.get("status")) == "审核"]
    pool = reviewed or candidates
    if len(pool) != 1:
        return None
    return pool[0]


def resolve_drawing_archive(
    *,
    material_no: str = "",
    drawing_no: str = "",
    drawing_archive_id: int | None = None,
    require_unique: bool = False,
) -> dict[str, Any] | None:
    """先按 id，再按物料号+图纸号（含 Rev 变体）。多条优先审核、再最新。"""
    if drawing_archive_id:
        row = fetch_one(
            """
            SELECT id, material_no, drawing_no, drawing_rev_no, standard_no, spec, model, spec_model,
                   material, unit_weight, od_d, mouth_n, thk_t, thk_f, seat_h_f, id_c, status
            FROM drawing_archives
            WHERE id=%s
            LIMIT 1
            """,
            (int(drawing_archive_id),),
        )
        if row:
            return row
    candidates = _drawing_candidates(material_no=material_no, drawing_no=drawing_no)
    if require_unique:
        return pick_unique_drawing(candidates)
    return candidates[0] if candidates else None


def set_material_drawing_archive_id(material_id: int, drawing_archive_id: int | None) -> None:
    ensure_material_drawing_archive_column()
    execute(
        "UPDATE materials SET drawing_archive_id=%s, updated_at=NOW() WHERE id=%s",
        (int(drawing_archive_id) if drawing_archive_id else None, int(material_id)),
    )


def backfill_material_drawing_archive_ids() -> dict[str, Any]:
    """历史物料按物料号+图纸号回填图纸 id。一对多/零条不瞎填。"""
    ensure_material_drawing_archive_column()
    rows = fetch_all(
        """
        SELECT id, material_no, drawing_no, standard_no, drawing_archive_id
        FROM materials
        WHERE drawing_archive_id IS NULL
        ORDER BY id
        """
    )
    linked = 0
    unmatched: list[dict[str, Any]] = []
    for row in rows:
        archive = resolve_drawing_archive(
            material_no=_norm(row.get("material_no")),
            drawing_no=_norm(row.get("drawing_no")),
            require_unique=True,
        )
        if not archive:
            unmatched.append({"material_id": int(row["id"]), "reason": "no_drawing_or_ambiguous"})
            continue
        execute(
            "UPDATE materials SET drawing_archive_id=%s WHERE id=%s",
            (int(archive["id"]), int(row["id"])),
        )
        linked += 1
    return {"linked": linked, "unmatched": len(unmatched), "unmatched_rows": unmatched[:200]}
