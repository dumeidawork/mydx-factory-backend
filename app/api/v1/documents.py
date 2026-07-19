"""单据中心API：预览、导出、状态查询。"""
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_accept_language
from app.core.database import execute, fetch_one
from app.core.paths import get_storage_dir
from app.domain.document_specs import (
    DOCUMENT_TYPES,
    FROZEN_FIELDS_V1,
    TEMPLATE_V1,
    get_document_label,
)
from app.services.in_memory_store import now_iso

router = APIRouter(prefix="/documents", tags=["单据中心"])

EXPORT_DIR = get_storage_dir() / "exports"
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


class DocumentRequest(BaseModel):
    document_type: str = Field(description="quality_certificate | packing_list | shipping_mark")
    language: str = "zh-CN"
    order_no: str
    payload: dict = Field(default_factory=dict)


def _validate_document_type(document_type: str) -> None:
    if document_type not in DOCUMENT_TYPES:
        raise HTTPException(status_code=400, detail=f"unsupported document_type: {document_type}")


def _build_document_payload(req: DocumentRequest) -> dict:
    fields = FROZEN_FIELDS_V1[req.document_type]
    doc_no = req.payload.get("document_no") or f"DOC-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    defaults = {
        "document_no": doc_no,
        "issue_date": datetime.now().strftime("%Y-%m-%d"),
        "client_name": req.payload.get("client_name", "待确认客户"),
        "contract_no": req.payload.get("contract_no", "N/A"),
        "order_no": req.order_no,
        "product_name": req.payload.get("product_name", "法兰"),
        "specification": req.payload.get("specification", "DN100"),
        "material": req.payload.get("material", "A105"),
        "standard": req.payload.get("standard", "ASME B16.5"),
        "quantity": req.payload.get("quantity", "100"),
        "inspector": req.payload.get("inspector", "QC-01"),
        "result": req.payload.get("result", "PASS"),
        "remark": req.payload.get("remark", ""),
        "box_no": req.payload.get("box_no", "1"),
        "total_boxes": req.payload.get("total_boxes", "1"),
        "gross_weight": req.payload.get("gross_weight", "100kg"),
        "net_weight": req.payload.get("net_weight", "95kg"),
        "destination_port": req.payload.get("destination_port", "Tokyo"),
        "mark_line_1": req.payload.get("mark_line_1", "MINGYUAN"),
        "mark_line_2": req.payload.get("mark_line_2", req.order_no),
        "mark_line_3": req.payload.get("mark_line_3", "MADE IN CHINA"),
    }
    return {field: defaults.get(field, "") for field in fields}


@router.get("/field-specs")
def get_field_specs():
    """返回冻结字段清单，便于前端动态渲染表单。"""
    return {"version": "v1.0", "fields": FROZEN_FIELDS_V1}


@router.post("/preview")
def preview_document(req: DocumentRequest, accept_language: str = Depends(get_accept_language)):
    _validate_document_type(req.document_type)
    lang = req.language or accept_language
    payload = _build_document_payload(req)
    doc_label = get_document_label(req.document_type, lang)
    template = TEMPLATE_V1[req.document_type]
    body = template["body"].format(doc_label=doc_label, **payload)
    return {
        "status": "success",
        "document_type": req.document_type,
        "language": lang,
        "title": template["title"].format(doc_label=doc_label, **payload),
        "fields": payload,
        "preview_text": body,
    }


@router.post("/export")
def export_document(req: DocumentRequest, accept_language: str = Depends(get_accept_language)):
    _validate_document_type(req.document_type)
    preview = preview_document(req, accept_language)

    filename = f"{req.document_type}_{req.order_no}_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    file_path = EXPORT_DIR / filename
    file_path.write_text(preview["preview_text"], encoding="utf-8")
    job_id = execute(
        """
        INSERT INTO document_jobs (doc_type, order_no, language, status, file_name, file_path)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (req.document_type, req.order_no, preview["language"], "completed", filename, str(file_path)),
    )
    meta = {
        "job_id": job_id,
        "status": "completed",
        "document_type": req.document_type,
        "order_no": req.order_no,
        "language": preview["language"],
        "file_name": filename,
        "file_path": str(file_path),
        "created_at": now_iso(),
    }
    return {
        "status": "success",
        "job_id": job_id,
        "download_url": f"/api/v1/documents/download/{job_id}",
        "meta": meta,
    }


@router.get("/{job_id}")
def get_document_job(job_id: int):
    job = fetch_one("SELECT id, doc_type, order_no, language, status, file_name, file_path, created_at FROM document_jobs WHERE id=%s", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "success", "job": job}


@router.get("/download/{job_id}")
def download_document(job_id: int):
    job = fetch_one("SELECT file_name, file_path FROM document_jobs WHERE id=%s", (job_id,))
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    file_path = Path(job["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="export file not found")
    return FileResponse(path=file_path, filename=job["file_name"], media_type="text/plain")
