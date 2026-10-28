"""二维码标签设计模板：主表 + 操作日志；可按打字要求明细套模板出 PDF。"""
from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Any, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing_extensions import Annotated

from app.api.deps import CurrentUser, get_current_user
from app.core.database import execute, fetch_all, fetch_one

router = APIRouter(prefix="/qr-label-designs", tags=["二维码标签设计"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]

_TABLES_READY = False

WIDTH_MIN, WIDTH_MAX = 50.0, 80.0
HEIGHT_MIN, HEIGHT_MAX = 25.0, 40.0
DATAMATRIX_MIN = 20.0
QUIET_ZONE = 2.5
CODE_BODY_MIN = 15.0

PAPER_SIZES_MM: dict[str, tuple[float, float]] = {
    "A3": (297.0, 420.0),
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "LETTER": (215.9, 279.4),
    "LABEL": (100.0, 150.0),
}

CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS qr_label_designs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            name VARCHAR(128) NOT NULL COMMENT '标签设计名称，唯一',
            width_mm DECIMAL(6,1) NOT NULL COMMENT '标签宽 mm',
            height_mm DECIMAL(6,1) NOT NULL COMMENT '标签高 mm',
            layout_json TEXT NOT NULL COMMENT '模块 JSON',
            print_json TEXT NOT NULL COMMENT '打印设置 JSON',
            created_by VARCHAR(64) NULL COMMENT '上传者',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(64) NULL COMMENT '更改者',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            UNIQUE KEY uk_qr_label_design_name (name),
            INDEX idx_qr_label_design_updated (updated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='二维码标签设计模板'
    """

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS qr_label_design_logs (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            row_id BIGINT NULL COMMENT '设计ID，删除后可空',
            name VARCHAR(128) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/copy/update/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_qr_label_log_row (row_id),
            INDEX idx_qr_label_log_name (name),
            INDEX idx_qr_label_log_action (action),
            INDEX idx_qr_label_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='二维码标签设计操作日志'
    """

SELECT_SQL = """
        id, name, width_mm, height_mm, layout_json, print_json,
        created_by, created_by_user_id, created_at,
        updated_by, updated_by_user_id, updated_at
    """


class PunchModule(BaseModel):
    id: str
    type: Literal["punch"]
    x_mm: float
    y_mm: float
    size_mm: float


class DataMatrixModule(BaseModel):
    id: str
    type: Literal["datamatrix"]
    x_mm: float
    y_mm: float
    size_mm: float
    quiet_mm: float | None = 2.5
    content: str = ""
    symbology: Literal["ecc200", "qr"] = "ecc200"


class TextModule(BaseModel):
    id: str
    type: Literal["text"]
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    content: str = ""
    font_mm: float | None = None
    font_pt: float | None = None
    font_family: str = "Calibri"
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: Literal["left", "center", "right"] = "left"
    wrap: bool = False


LabelModule = Annotated[Union[PunchModule, DataMatrixModule, TextModule], Field(discriminator="type")]


class PrintSettings(BaseModel):
    paper: str = "A4"
    orientation: Literal["portrait", "landscape"] = "landscape"
    paper_w_mm: float | None = None
    paper_h_mm: float | None = None
    cols: int = 1
    rows: int = 1
    gap_x_mm: float = 1
    gap_y_mm: float = 1
    margin_mm: float = 5


class DesignPayload(BaseModel):
    name: str
    width_mm: float
    height_mm: float
    modules: list[LabelModule] = Field(default_factory=list)
    print: PrintSettings = Field(default_factory=PrintSettings)


class CopyRequest(BaseModel):
    name: str


class RenderItem(BaseModel):
    order_no: str = ""
    factory_order_no: str = ""
    item_no: int | None = None
    drawing_rev_no: str = ""
    material_no: str = ""
    part_name: str = ""
    typing_content: str = ""
    spec: str = ""
    model_standard: str = ""
    qr_spec: str = ""
    heat_no: str = ""
    heat_treatment_batch_no: str = ""
    quantity: int = 0
    unit_weight: float | None = None
    remark: str = ""
    supplier_no: str = ""


class RenderRequest(BaseModel):
    items: list[RenderItem] = Field(default_factory=list)


class EncodePreviewRequest(BaseModel):
    template: str = ""
    values: dict[str, str] = Field(default_factory=dict)
    symbology: Literal["ecc200", "qr"] = "ecc200"


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


def _snap(value: float) -> float:
    return round(float(value) * 2) / 2


def _snap_tenth(value: float) -> float:
    return round(float(value) * 10) / 10


def _is_half_step(value: float) -> bool:
    return abs(value * 2 - round(value * 2)) < 1e-6


def _paper_size(settings: PrintSettings) -> tuple[float, float]:
    paper = (settings.paper or "A4").upper()
    if paper == "CUSTOM":
        width = float(settings.paper_w_mm or 0)
        height = float(settings.paper_h_mm or 0)
        if width < 20 or height < 20:
            raise HTTPException(status_code=400, detail="自定义纸张宽高须至少 20 mm")
        return _snap(width), _snap(height)
    size = PAPER_SIZES_MM.get(paper)
    if not size:
        raise HTTPException(status_code=400, detail=f"不支持的纸张：{settings.paper}")
    width, height = size
    if settings.orientation == "landscape":
        return height, width
    return width, height


def _max_count(paper: float, label: float, gap: float, margin: float) -> int:
    usable = paper - margin * 2
    if usable < label:
        return 0
    return int(math.floor((usable + gap) / (label + gap)))


def _validate_half(name: str, value: float) -> float:
    snapped = _snap(value)
    if not _is_half_step(value):
        raise HTTPException(status_code=400, detail=f"{name}须为 0.5 mm 的倍数")
    return snapped


def _module_box(module: LabelModule) -> tuple[float, float, float, float]:
    if module.type == "text":
        return module.x_mm, module.y_mm, module.w_mm, module.h_mm
    return module.x_mm, module.y_mm, module.size_mm, module.size_mm


def validate_design(payload: DesignPayload) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = _normalize_str(payload.name)
    if not name:
        raise HTTPException(status_code=400, detail="标签名不能为空")
    if len(name) > 128:
        raise HTTPException(status_code=400, detail="标签名不能超过 128 字")

    width = _validate_half("标签宽度", payload.width_mm)
    height = _validate_half("标签高度", payload.height_mm)
    if width < WIDTH_MIN or width > WIDTH_MAX:
        raise HTTPException(status_code=400, detail=f"标签宽度须在 {WIDTH_MIN:g}–{WIDTH_MAX:g} mm")
    if height < HEIGHT_MIN or height > HEIGHT_MAX:
        raise HTTPException(status_code=400, detail=f"标签高度须在 {HEIGHT_MIN:g}–{HEIGHT_MAX:g} mm")

    modules: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    datamatrix_count = 0
    for idx, raw in enumerate(payload.modules, start=1):
        module_id = _normalize_str(raw.id) or f"m-{idx}"
        if module_id in seen_ids:
            raise HTTPException(status_code=400, detail=f"模块 id 重复：{module_id}")
        seen_ids.add(module_id)
        x = _validate_half(f"第{idx}个模块 X", raw.x_mm)
        y = _validate_half(f"第{idx}个模块 Y", raw.y_mm)
        if raw.type == "text":
            w = _validate_half(f"第{idx}个文本宽", raw.w_mm)
            h = _validate_half(f"第{idx}个文本高", raw.h_mm)
            if w < 4 or h < 3:
                raise HTTPException(status_code=400, detail=f"第{idx}个文本框过小（最小 4×3 mm）")
            box = (x, y, w, h)
            font_mm = raw.font_mm
            if font_mm is None:
                font_mm = float(raw.font_pt or 6) * 0.3528
            item: dict[str, Any] = {
                "id": module_id,
                "type": "text",
                "x_mm": x,
                "y_mm": y,
                "w_mm": w,
                "h_mm": h,
                "content": raw.content or "",
                "font_mm": max(0.0, min(8.0, round(float(font_mm), 3))),
                "font_family": _normalize_str(raw.font_family) or "Calibri",
                "bold": bool(raw.bold),
                "italic": bool(raw.italic),
                "underline": bool(raw.underline),
                "align": raw.align,
                "wrap": bool(raw.wrap),
            }
        else:
            size = _validate_half(f"第{idx}个模块边长", raw.size_mm)
            if raw.type == "punch":
                if size < 3:
                    raise HTTPException(status_code=400, detail=f"第{idx}个打孔器直径须至少 3 mm")
                item = {
                    "id": module_id,
                    "type": raw.type,
                    "x_mm": x,
                    "y_mm": y,
                    "size_mm": size,
                }
            else:
                datamatrix_count += 1
                quiet = _snap_tenth(float(raw.quiet_mm if raw.quiet_mm is not None else QUIET_ZONE))
                if quiet < 0:
                    raise HTTPException(status_code=400, detail="净空不能为负")
                if quiet * 2 - size > 1e-6:
                    raise HTTPException(status_code=400, detail="净空过大，Data Matrix 边长会小于 0")
                if size < 1:
                    raise HTTPException(status_code=400, detail="码区边长须大于 0")
                item = {
                    "id": module_id,
                    "type": raw.type,
                    "x_mm": x,
                    "y_mm": y,
                    "size_mm": size,
                    "quiet_mm": quiet,
                    "content": raw.content or "",
                    "symbology": raw.symbology if raw.symbology == "qr" else "ecc200",
                }
            box = (x, y, size, size)
        bx, by, bw, bh = box
        if bx < -1e-6 or by < -1e-6 or bx + bw - width > 1e-6 or by + bh - height > 1e-6:
            raise HTTPException(status_code=400, detail=f"第{idx}个模块超出画布，请拖回标签范围内")
        modules.append(item)

    if datamatrix_count > 1:
        raise HTTPException(status_code=400, detail="一张标签最多放置 1 个二维码生成区域")

    print_in = payload.print
    gap_x = _validate_half("横间隙", print_in.gap_x_mm)
    gap_y = _validate_half("竖间隙", print_in.gap_y_mm)
    margin = _validate_half("页边距", print_in.margin_mm)
    if gap_x < 0 or gap_y < 0:
        raise HTTPException(status_code=400, detail="标签间隙不能为负")
    if margin < 0:
        raise HTTPException(status_code=400, detail="页边距不能为负")
    cols = int(print_in.cols or 0)
    rows = int(print_in.rows or 0)
    if cols < 1 or rows < 1:
        raise HTTPException(status_code=400, detail="每页横/竖个数须为正整数")

    paper_w, paper_h = _paper_size(print_in)
    max_cols = _max_count(paper_w, width, gap_x, margin)
    max_rows = _max_count(paper_h, height, gap_y, margin)
    if max_cols < 1 or max_rows < 1:
        raise HTTPException(status_code=400, detail="当前纸张放不下这一枚标签，请改纸张或缩小标签")
    if cols > max_cols or rows > max_rows:
        raise HTTPException(
            status_code=400,
            detail=f"当前纸张最多横 {max_cols} × 竖 {max_rows}",
        )

    paper_key = (print_in.paper or "A4").upper()
    print_out = {
        "paper": paper_key,
        "orientation": print_in.orientation,
        "paper_w_mm": _snap(print_in.paper_w_mm) if paper_key == "CUSTOM" and print_in.paper_w_mm else None,
        "paper_h_mm": _snap(print_in.paper_h_mm) if paper_key == "CUSTOM" and print_in.paper_h_mm else None,
        "cols": cols,
        "rows": rows,
        "gap_x_mm": gap_x,
        "gap_y_mm": gap_y,
        "margin_mm": margin,
    }
    return modules, print_out


def default_print(width_mm: float, height_mm: float) -> dict[str, Any]:
    gap, margin = 1.0, 5.0
    paper_w, paper_h = PAPER_SIZES_MM["A4"]
    paper_w, paper_h = paper_h, paper_w
    return {
        "paper": "A4",
        "orientation": "landscape",
        "paper_w_mm": None,
        "paper_h_mm": None,
        "cols": max(1, _max_count(paper_w, width_mm, gap, margin)),
        "rows": max(1, _max_count(paper_h, height_mm, gap, margin)),
        "gap_x_mm": gap,
        "gap_y_mm": gap,
        "margin_mm": margin,
    }


def _parse_json(raw: Any, fallback: Any) -> Any:
    if raw is None or raw == "":
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _row_from_db(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    modules = _parse_json(row.get("layout_json"), [])
    print_settings = _parse_json(row.get("print_json"), default_print(70, 32))
    return {
        "id": int(row["id"]),
        "name": _normalize_str(row.get("name")),
        "width_mm": float(row.get("width_mm") or 0),
        "height_mm": float(row.get("height_mm") or 0),
        "modules": modules,
        "print": print_settings,
        "created_by": _normalize_str(row.get("created_by")),
        "created_by_user_id": row.get("created_by_user_id"),
        "created_at": _jsonable(row.get("created_at")) or "",
        "updated_by": _normalize_str(row.get("updated_by")),
        "updated_by_user_id": row.get("updated_by_user_id"),
        "updated_at": _jsonable(row.get("updated_at")) or "",
    }


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _write_log(
    *,
    action: str,
    user: CurrentUser,
    row_id: int | None,
    name: str,
    change_summary: dict[str, Any],
) -> None:
    execute(
        """
        INSERT INTO qr_label_design_logs
            (row_id, name, action, operator_user_id, operator_name, change_summary)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            row_id,
            name,
            action,
            user.id,
            user.name,
            json.dumps(change_summary, ensure_ascii=False, default=str),
        ),
    )


def _find_by_name(name: str, exclude_id: int | None = None) -> dict[str, Any] | None:
    if exclude_id is None:
        return fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE name=%s", (name,))
    return fetch_one(
        f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE name=%s AND id<>%s",
        (name, exclude_id),
    )


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row.get("name"),
        "width_mm": row.get("width_mm"),
        "height_mm": row.get("height_mm"),
        "module_count": len(row.get("modules") or []),
        "print": row.get("print"),
    }


@router.post("/encode-preview")
def encode_preview(req: EncodePreviewRequest, _user: UserDep):
    from app.services.qr_label_render import resolve_datamatrix_payload
    from app.utils.datamatrix_ecc200 import encode_datamatrix
    from app.utils.qrcode_label import encode_qr_matrix

    payload = resolve_datamatrix_payload(dict(req.values or {}), req.template or "")
    symbology = "qr" if req.symbology == "qr" else "ecc200"
    matrix = encode_qr_matrix(payload) if symbology == "qr" else encode_datamatrix(payload, min_symbol=32)
    return {
        "status": "success",
        "payload": payload,
        "symbology": symbology,
        "size": len(matrix),
        "matrix": matrix,
    }


@router.get("")
def list_designs(_user: UserDep, name: str = "", limit: int = Query(500, ge=1, le=2000)):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    query = _normalize_str(name)
    if query:
        clauses.append("name LIKE %s")
        params.append(f"%{query}%")
    rows = fetch_all(
        f"""
        SELECT {SELECT_SQL}
        FROM qr_label_designs
        WHERE {' AND '.join(clauses)}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        (*params, limit),
    )
    data = [_row_from_db(r) for r in rows]
    return {"status": "success", "total": len(data), "rows": data}


@router.get("/logs")
def list_logs(
    _user: UserDep,
    name: str = "",
    action: str = "",
    limit: int = Query(200, ge=1, le=500),
):
    _ensure_tables()
    clauses = ["1=1"]
    params: list[Any] = []
    if _normalize_str(name):
        clauses.append("name LIKE %s")
        params.append(f"%{_normalize_str(name)}%")
    if _normalize_str(action):
        clauses.append("action=%s")
        params.append(_normalize_str(action))
    rows = fetch_all(
        f"""
        SELECT id, row_id, name, action, operator_user_id, operator_name,
               operated_at, change_summary
        FROM qr_label_design_logs
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


@router.get("/{row_id}")
def get_design(row_id: int, _user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到该标签设计")
    return {"status": "success", "row": _row_from_db(current)}


@router.post("")
def create_design(req: DesignPayload, user: UserDep):
    _ensure_tables()
    modules, print_out = validate_design(req)
    name = _normalize_str(req.name)
    if _find_by_name(name):
        raise HTTPException(status_code=400, detail=f"标签名已存在：{name}")
    width = _snap(req.width_mm)
    height = _snap(req.height_mm)
    new_id = execute(
        """
        INSERT INTO qr_label_designs (
            name, width_mm, height_mm, layout_json, print_json,
            created_by, created_by_user_id, updated_by, updated_by_user_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (name, width, height, _dump(modules), _dump(print_out), user.name, user.id, user.name, user.id),
    )
    after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (new_id,)))
    _write_log(
        action="create",
        user=user,
        row_id=new_id,
        name=name,
        change_summary={"op": "create", "fields": _compact(after)},
    )
    return {"status": "success", "row": after}


@router.post("/{row_id}/copy")
def copy_design(row_id: int, req: CopyRequest, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到要复制的标签设计")
    name = _normalize_str(req.name)
    if not name:
        raise HTTPException(status_code=400, detail="新标签名不能为空")
    if _find_by_name(name):
        raise HTTPException(status_code=400, detail=f"标签名已存在：{name}")
    source = _row_from_db(current)
    new_id = execute(
        """
        INSERT INTO qr_label_designs (
            name, width_mm, height_mm, layout_json, print_json,
            created_by, created_by_user_id, updated_by, updated_by_user_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            source["width_mm"],
            source["height_mm"],
            _dump(source["modules"]),
            _dump(source["print"]),
            user.name,
            user.id,
            user.name,
            user.id,
        ),
    )
    after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (new_id,)))
    _write_log(
        action="copy",
        user=user,
        row_id=new_id,
        name=name,
        change_summary={"op": "copy", "from_id": row_id, "from_name": source["name"], "fields": _compact(after)},
    )
    return {"status": "success", "row": after}


@router.post("/{row_id}/render")
def render_design(row_id: int, req: RenderRequest, user: UserDep):
    import io
    import re
    from urllib.parse import quote

    from fastapi.responses import StreamingResponse

    from app.services.qr_label_render import label_page_count, render_label_pdf

    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到该标签设计")
    design = _row_from_db(current)
    items = [item.model_dump() for item in req.items]
    if not items:
        raise HTTPException(status_code=400, detail="请选择要生成标签的明细")
    if not any(int(item.get("quantity") or 0) > 0 for item in items):
        raise HTTPException(status_code=400, detail="所选明细的数量均为 0，无法生成标签")
    try:
        pdf = render_label_pdf(design, items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pages = sum(label_page_count(int(item.get("quantity") or 0), design.get("print") or {}) for item in items)
    labels = sum(max(0, int(item.get("quantity") or 0)) for item in items)
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", design["name"] or "标签")
    file_name = f"{safe_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    _write_log(
        action="render",
        user=user,
        row_id=row_id,
        name=design["name"],
        change_summary={"op": "render", "file_name": file_name, "item_count": len(items), "label_count": labels, "page_count": pages},
    )
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(file_name)}"},
    )


@router.put("/{row_id}")
def update_design(row_id: int, req: DesignPayload, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到该标签设计")
    modules, print_out = validate_design(req)
    name = _normalize_str(req.name)
    if _find_by_name(name, exclude_id=row_id):
        raise HTTPException(status_code=400, detail=f"标签名已存在：{name}")
    before = _row_from_db(current)
    width = _snap(req.width_mm)
    height = _snap(req.height_mm)
    execute(
        """
        UPDATE qr_label_designs
        SET name=%s, width_mm=%s, height_mm=%s, layout_json=%s, print_json=%s,
            updated_by=%s, updated_by_user_id=%s, updated_at=NOW()
        WHERE id=%s
        """,
        (name, width, height, _dump(modules), _dump(print_out), user.name, user.id, row_id),
    )
    after = _row_from_db(fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,)))
    fields: dict[str, Any] = {}
    for key in ("name", "width_mm", "height_mm"):
        if before.get(key) != after.get(key):
            fields[key] = {"before": before.get(key), "after": after.get(key)}
    if _dump(before.get("modules")) != _dump(after.get("modules")):
        fields["modules"] = {"before_count": len(before.get("modules") or []), "after_count": len(after.get("modules") or [])}
    if _dump(before.get("print")) != _dump(after.get("print")):
        fields["print"] = {"before": before.get("print"), "after": after.get("print")}
    if fields:
        _write_log(
            action="update",
            user=user,
            row_id=row_id,
            name=name,
            change_summary={"op": "update", "fields": fields},
        )
    return {"status": "success", "row": after}


@router.delete("/{row_id}")
def delete_design(row_id: int, user: UserDep):
    _ensure_tables()
    current = fetch_one(f"SELECT {SELECT_SQL} FROM qr_label_designs WHERE id=%s", (row_id,))
    if not current:
        raise HTTPException(status_code=404, detail="未找到该标签设计")
    before = _row_from_db(current)
    execute("DELETE FROM qr_label_designs WHERE id=%s", (row_id,))
    _write_log(
        action="delete",
        user=user,
        row_id=row_id,
        name=before["name"],
        change_summary={"op": "delete", "fields": _compact(before)},
    )
    return {"status": "success", "id": row_id}
