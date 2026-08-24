"""法国许可证：行数据、合同附件与质量合格证书生成。"""
from __future__ import annotations

import os
import random
import shutil
import tempfile
import zipfile
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.core.database import execute, fetch_all, fetch_one
from app.core.paths import get_templates_dir
from app.services.license_schema import CREATE_LICENSE_CERTIFICATE_ITEMS_SQL, CREATE_LICENSE_DOCS_SQL
from app.services.template_renderer import render_docx_template, render_xlsx_template

GRADE_A105 = "A105"
GRADE_304 = "304"
GRADE_316 = "316"
KNOWN_GRADES = (GRADE_A105, GRADE_304, GRADE_316)
PAGE_CAPACITY = {GRADE_A105: 6, GRADE_304: 4, GRADE_316: 3}
CERT_SUFFIX = {GRADE_A105: "1", GRADE_304: "2", GRADE_316: "2"}

CHEM_RANGES: dict[str, dict[str, tuple[str, str]]] = {
    GRADE_A105: {
        "c": ("0.1", "0.35"),
        "si": ("0.1", "0.35"),
        "mn": ("0.6", "1.05"),
        "s": ("0.01", "0.04"),
        "p": ("0.01", "0.035"),
        "cr": ("0.01", "0.25"),
        "ni": ("0.1", "0.4"),
        "mo": ("0.05", "0.12"),
        "cu": ("0.1", "0.4"),
    },
    GRADE_304: {
        "c": ("0.01", "0.07"),
        "si": ("0.01", "1.00"),
        "mn": ("0.01", "2.00"),
        "s": ("0.001", "0.015"),
        "p": ("0.01", "0.045"),
        "cr": ("18.0", "20.0"),
        "ni": ("8.0", "10.5"),
    },
    GRADE_316: {
        "c": ("0.01", "0.03"),
        "si": ("0.1", "1.00"),
        "mn": ("0.1", "2.00"),
        "s": ("0.001", "0.015"),
        "p": ("0.01", "0.045"),
        "cr": ("16.0", "18.0"),
        "ni": ("10.0", "14.0"),
    },
}

MECH_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    GRADE_A105: {"yield": (250, 370), "tensile": (485, 590), "elong": (22, 40)},
    GRADE_304: {"yield": (210, 330), "tensile": (520, 650), "elong": (30, 45)},
    GRADE_316: {"yield": (210, 330), "tensile": (520, 650), "elong": (30, 45)},
}

OUTPUT_ATTACHMENT = {
    "CS": "{order_no}合同附件（碳钢）.xlsx",
    "SS": "{order_no}合同附件（不锈钢）.xlsx",
}
OUTPUT_CERTIFICATE = {
    GRADE_A105: "{order_no}质量合格证书 （碳钢）.docx",
    GRADE_304: "{order_no}量合格证书（不锈钢）_304.docx",
    GRADE_316: "{order_no}量合格证书（不锈钢）_316.docx",
}

_TABLES_READY = False


def _ensure_tables() -> None:
    global _TABLES_READY
    if _TABLES_READY:
        return
    execute(CREATE_LICENSE_DOCS_SQL)
    execute(CREATE_LICENSE_CERTIFICATE_ITEMS_SQL)
    _TABLES_READY = True


def normalize_order_no(order_no: str) -> str:
    text = str(order_no or "").strip()
    upper = text.upper()
    if upper.startswith("PO"):
        text = text[2:].strip()
    return text


def is_france_order_no(order_no: str) -> bool:
    return normalize_order_no(order_no).startswith("2")


def classify_grade(material: str) -> str | None:
    text = str(material or "").upper().replace(" ", "")
    if "316" in text:
        return GRADE_316
    if "304" in text:
        return GRADE_304
    if "A105" in text:
        return GRADE_A105
    return None


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _to_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(Decimal(str(value)))
    except Exception:
        return 0


def format_amount(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def ceil_class_amount(amount: Decimal) -> Decimal:
    integral = amount.to_integral_value(rounding=ROUND_HALF_UP)
    if amount == integral:
        return integral
    return amount.to_integral_value(rounding=ROUND_CEILING)


def calc_unit_price(class_amount: Decimal, class_weight: Decimal) -> Decimal | None:
    if class_weight <= 0:
        return None
    return (ceil_class_amount(class_amount) / class_weight).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def format_unit_price(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}"


def format_item_no(raw: Any, pad: bool) -> str:
    if raw is None or raw == "":
        return ""
    text = str(raw).strip()
    if not pad:
        return text
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        return str(abs(int(text))).zfill(5)
    return text.zfill(5) if text.isalnum() else text


def format_batch_date(value: date | datetime | str | None) -> str:
    if value is None or value == "":
        value = date.today()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                value = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        else:
            return text
    return f"{value.year}年{value.month}月{value.day}日"


def parse_batch_date(value: date | datetime | str | None) -> date:
    if value is None or value == "":
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError("批量年月日格式无效，请使用 YYYY-MM-DD")


def _po_variants(order_no: str) -> tuple[str, str, str]:
    digits = normalize_order_no(order_no)
    raw = str(order_no or "").strip()
    return raw or digits, digits, f"PO{digits}"


def _decimal_places(lo: str, hi: str) -> int:
    def places(text: str) -> int:
        return len(text.split(".", 1)[1]) if "." in text else 0

    return max(places(lo), places(hi))


def _rand_closed_decimal(lo: str, hi: str) -> str:
    places = _decimal_places(lo, hi)
    step = Decimal(10) ** -places
    start = Decimal(lo)
    end = Decimal(hi)
    steps = int((end - start) / step) + 1
    value = start + step * random.randrange(max(steps, 1))
    return f"{value:.{places}f}"


def _rand_closed_int(lo: int, hi: int) -> str:
    return str(random.randint(lo, hi))


def load_license_rows(order_no: str) -> dict[str, Any]:
    _ensure_tables()
    digits = normalize_order_no(order_no)
    raw, digits_only, po_prefixed = _po_variants(order_no)
    packing_rows = fetch_all(
        """
        SELECT i.id, i.list_id, i.seq, i.pos, i.po, i.quantity, i.unit_weight, i.product_unit_price,
               i.spec_model, i.material, i.material_no
        FROM france_customer_packing_list_items i
        WHERE TRIM(i.po) IN (%s, %s, %s)
        ORDER BY i.list_id, i.seq, i.id
        """,
        (raw, digits_only, po_prefixed),
    )
    source = "packing"
    raw_rows = packing_rows
    if not packing_rows:
        source = "order_details"
        raw_rows = fetch_all(
            """
            SELECT id, seq, item_no, quantity, unit_weight, product_unit_price, spec_model, material, material_no
            FROM order_details
            WHERE order_no = %s OR order_no = %s
            ORDER BY seq, item_no, id
            """,
            (raw, digits),
        )

    items: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in raw_rows:
        material = str(row.get("material") or "")
        grade = classify_grade(material)
        if source == "packing":
            line_key = f"p:{int(row['id'])}"
            item_no_raw = row.get("pos")
        else:
            line_key = f"d:{int(row['id'])}"
            item_no_raw = row.get("item_no")
        qty = _to_int(row.get("quantity"))
        unit_price = _to_decimal(row.get("product_unit_price"))
        unit_weight = _to_decimal(row.get("unit_weight"))
        amount = unit_price * Decimal(qty)
        weight = unit_weight * Decimal(qty)
        payload = {
            "line_key": line_key,
            "source_id": int(row["id"]),
            "item_no_raw": "" if item_no_raw is None else str(item_no_raw),
            "qty": qty,
            "unit_price": format_amount(unit_price),
            "unit_weight": format_amount(unit_weight),
            "amount": amount,
            "weight": weight,
            "amount_text": format_amount(amount),
            "weight_text": format_amount(weight),
            "remark": "",
            "spec_model": str(row.get("spec_model") or ""),
            "material": material,
            "material_no": str(row.get("material_no") or ""),
            "grade": grade,
        }
        if grade:
            items.append(payload)
        else:
            skipped.append(payload)

    return {
        "order_no": digits,
        "source": source,
        "items": items,
        "skipped": skipped,
        "has_source": bool(raw_rows),
    }


def summarize_rows(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_grade: dict[str, list[dict[str, Any]]] = {g: [] for g in KNOWN_GRADES}
    for item in items:
        grade = item.get("grade")
        if grade in by_grade:
            by_grade[grade].append(item)

    def totals(rows: list[dict[str, Any]]) -> tuple[Decimal, Decimal]:
        amount = sum((row["amount"] for row in rows), Decimal("0"))
        weight = sum((row["weight"] for row in rows), Decimal("0"))
        return amount, weight

    cs_amount, cs_weight = totals(by_grade[GRADE_A105])
    ss_rows = by_grade[GRADE_304] + by_grade[GRADE_316]
    ss_amount, ss_weight = totals(ss_rows)
    contract_amount = cs_amount + ss_amount
    contract_weight = cs_weight + ss_weight
    planned: list[str] = []
    if by_grade[GRADE_A105]:
        planned.append(OUTPUT_ATTACHMENT["CS"])
        planned.append(OUTPUT_CERTIFICATE[GRADE_A105])
    if ss_rows:
        planned.append(OUTPUT_ATTACHMENT["SS"])
    if by_grade[GRADE_304]:
        planned.append(OUTPUT_CERTIFICATE[GRADE_304])
    if by_grade[GRADE_316]:
        planned.append(OUTPUT_CERTIFICATE[GRADE_316])
    return {
        "by_grade": by_grade,
        "cs_amount": cs_amount,
        "cs_weight": cs_weight,
        "cs_unit_price": calc_unit_price(cs_amount, cs_weight),
        "ss_amount": ss_amount,
        "ss_weight": ss_weight,
        "ss_unit_price": calc_unit_price(ss_amount, ss_weight),
        "contract_amount": contract_amount,
        "contract_weight": contract_weight,
        "planned_files": planned,
        "counts": {
            GRADE_A105: len(by_grade[GRADE_A105]),
            GRADE_304: len(by_grade[GRADE_304]),
            GRADE_316: len(by_grade[GRADE_316]),
        },
    }


def preview_payload(order_no: str) -> dict[str, Any]:
    loaded = load_license_rows(order_no)
    summary = summarize_rows(loaded["items"])
    digits = loaded["order_no"]
    planned = [name.format(order_no=digits) for name in summary["planned_files"]]
    return {
        "order_no": digits,
        "source": loaded["source"],
        "has_source": loaded["has_source"],
        "counts": {**summary["counts"], "unknown": len(loaded["skipped"])},
        "items": [
            {
                **{k: v for k, v in item.items() if k not in {"amount", "weight"}},
                "amount": item["amount_text"],
                "weight": item["weight_text"],
            }
            for item in loaded["items"]
        ],
        "skipped": [
            {
                **{k: v for k, v in item.items() if k not in {"amount", "weight"}},
                "amount": item["amount_text"],
                "weight": item["weight_text"],
            }
            for item in loaded["skipped"]
        ],
        "totals": {
            "contract_amount": format_amount(summary["contract_amount"]),
            "contract_weight": format_amount(summary["contract_weight"]),
            "cs_amount": format_amount(summary["cs_amount"]),
            "cs_weight": format_amount(summary["cs_weight"]),
            "cs_unit_price": format_unit_price(summary["cs_unit_price"]),
            "ss_amount": format_amount(summary["ss_amount"]),
            "ss_weight": format_amount(summary["ss_weight"]),
            "ss_unit_price": format_unit_price(summary["ss_unit_price"]),
        },
        "planned_files": planned,
    }


def _template_dir() -> Path:
    return get_templates_dir() / "20-FR-xkz"


def attachment_template(material_class: str) -> Path:
    if material_class == "CS":
        return _template_dir() / "01-htfj" / "A105" / "contract_attachment.xlsx"
    return _template_dir() / "01-htfj" / "304&316" / "contract_attachment.xlsx"


def certificate_template(grade: str) -> Path:
    return _template_dir() / "00-hgzs" / grade / "quality_certificate.docx"


def _require_template(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"许可证模板不存在：{path}")
    return path


def _locked_or_random_values(order_no: str, item: dict[str, Any]) -> dict[str, str]:
    grade = str(item["grade"])
    existing = fetch_one(
        """
        SELECT chem_c, chem_si, chem_mn, chem_s, chem_p, chem_cr, chem_ni, chem_mo, chem_cu,
               mech_yield, mech_tensile, mech_elong
        FROM license_certificate_items
        WHERE order_no=%s AND grade=%s AND line_key=%s
        """,
        (order_no, grade, item["line_key"]),
    )
    if existing:
        return {
            "c": str(existing.get("chem_c") or ""),
            "si": str(existing.get("chem_si") or ""),
            "mn": str(existing.get("chem_mn") or ""),
            "s": str(existing.get("chem_s") or ""),
            "p": str(existing.get("chem_p") or ""),
            "cr": str(existing.get("chem_cr") or ""),
            "ni": str(existing.get("chem_ni") or ""),
            "mo": str(existing.get("chem_mo") or ""),
            "cu": str(existing.get("chem_cu") or ""),
            "yield": str(existing.get("mech_yield") or ""),
            "tensile": str(existing.get("mech_tensile") or ""),
            "elong": str(existing.get("mech_elong") or ""),
        }

    chem: dict[str, str] = {}
    for key, (lo, hi) in CHEM_RANGES[grade].items():
        chem[key] = _rand_closed_decimal(lo, hi)
    if grade != GRADE_A105:
        chem.setdefault("mo", "")
        chem.setdefault("cu", "")
    mech = {key: _rand_closed_int(lo, hi) for key, (lo, hi) in MECH_RANGES[grade].items()}
    values = {**chem, **mech}
    execute(
        """
        INSERT INTO license_certificate_items (
            order_no, grade, line_key, spec_model, qty,
            chem_c, chem_si, chem_mn, chem_s, chem_p, chem_cr, chem_ni, chem_mo, chem_cu,
            mech_yield, mech_tensile, mech_elong
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            order_no,
            grade,
            item["line_key"],
            item.get("spec_model") or "",
            int(item.get("qty") or 0),
            values.get("c", ""),
            values.get("si", ""),
            values.get("mn", ""),
            values.get("s", ""),
            values.get("p", ""),
            values.get("cr", ""),
            values.get("ni", ""),
            values.get("mo", ""),
            values.get("cu", ""),
            values.get("yield", ""),
            values.get("tensile", ""),
            values.get("elong", ""),
        ),
    )
    return values


def _empty_row_mapping(capacity: int) -> dict[str, str]:
    keys = ("spec", "qty", "c", "si", "mn", "s", "p", "cr", "ni", "mo", "cu", "yield", "tensile", "elong")
    return {f"r{idx}_{key}": "" for idx in range(1, capacity + 1) for key in keys}


def _chunk(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _force_inner_sectpr_continuous(root) -> None:
    """模板里误存的“下一页”分节会把同一张合格证拆成多页，合页时一律改成连续。"""
    body = root.find(qn("w:body")) if root.tag != qn("w:body") else root
    body_sect = body.find(qn("w:sectPr")) if body is not None else None
    for sect in root.iter(qn("w:sectPr")):
        if sect is body_sect:
            continue
        typ = sect.find(qn("w:type"))
        if typ is None:
            typ = OxmlElement("w:type")
            sect.insert(0, typ)
        typ.set(qn("w:val"), "continuous")


def _is_page_break_only_paragraph(element) -> bool:
    if element.tag != qn("w:p"):
        return False
    texts = "".join(t.text or "" for t in element.iter(qn("w:t"))).strip()
    if texts:
        return False
    return any(br.get(qn("w:type")) == "page" for br in element.iter(qn("w:br")))


def _insert_before_body_sectpr(body, element) -> None:
    sect = body.find(qn("w:sectPr"))
    if sect is not None:
        sect.addprevious(element)
    else:
        body.append(element)


def _add_single_page_break(body) -> None:
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    run.append(br)
    paragraph.append(run)
    _insert_before_body_sectpr(body, paragraph)


def _merge_docx_pages(page_paths: list[Path], output_path: Path) -> None:
    if not page_paths:
        raise ValueError("没有可合并的合格证页")
    master = Document(str(page_paths[0]))
    _force_inner_sectpr_continuous(master.element)
    body = master.element.body
    for path in page_paths[1:]:
        _add_single_page_break(body)
        src = Document(str(path))
        _force_inner_sectpr_continuous(src.element)
        for child in list(src.element.body):
            if child.tag == qn("w:sectPr"):
                continue
            if _is_page_break_only_paragraph(child):
                continue
            _insert_before_body_sectpr(body, deepcopy(child))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    master.save(str(output_path))


def _render_attachment(
    order_no: str,
    material_class: str,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    pad_item_no: bool,
    output_path: Path,
) -> None:
    class_amount = summary["cs_amount"] if material_class == "CS" else summary["ss_amount"]
    class_weight = summary["cs_weight"] if material_class == "CS" else summary["ss_weight"]
    unit_price = summary["cs_unit_price"] if material_class == "CS" else summary["ss_unit_price"]
    items = [
        {
            "item_no": format_item_no(row.get("item_no_raw"), pad_item_no),
            "amount": format_amount(row["amount"]),
            "qty": row["qty"],
            "weight": format_amount(row["weight"]),
            "remark": row.get("remark") or "",
        }
        for row in rows
    ]
    mapping = {
        "order_no": order_no,
        "class_amount": format_amount(class_amount),
        "class_weight": format_amount(class_weight),
        "contract_amount": format_amount(summary["contract_amount"]),
        "contract_weight": format_amount(summary["contract_weight"]),
        "unit_price": format_unit_price(unit_price),
    }
    render_xlsx_template(_require_template(attachment_template(material_class)), output_path, mapping, items=items)


def _render_certificate(
    order_no: str,
    grade: str,
    rows: list[dict[str, Any]],
    inspector_name: str,
    batch_date_text: str,
    output_path: Path,
) -> None:
    capacity = PAGE_CAPACITY[grade]
    template = _require_template(certificate_template(grade))
    work = Path(tempfile.mkdtemp(prefix="license_cert_"))
    try:
        page_paths: list[Path] = []
        for page_idx, chunk in enumerate(_chunk(rows, capacity), start=1):
            mapping = _empty_row_mapping(capacity)
            mapping.update(
                {
                    "order_no": order_no,
                    "cert_no": f"{order_no}{CERT_SUFFIX[grade]}",
                    "batch_date": batch_date_text,
                    "inspector_name": inspector_name,
                }
            )
            for slot, item in enumerate(chunk, start=1):
                values = _locked_or_random_values(order_no, item)
                mapping[f"r{slot}_spec"] = str(item.get("spec_model") or "")
                mapping[f"r{slot}_qty"] = "" if item.get("qty") in (None, "") else str(item["qty"])
                for key in ("c", "si", "mn", "s", "p", "cr", "ni", "mo", "cu", "yield", "tensile", "elong"):
                    mapping[f"r{slot}_{key}"] = values.get(key, "")
            page_path = work / f"page_{page_idx}.docx"
            render_docx_template(template, page_path, mapping)
            page_paths.append(page_path)
        _merge_docx_pages(page_paths, output_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unique_arcname(used: set[str], name: str) -> str:
    safe = Path(name).name or "file"
    if safe not in used:
        used.add(safe)
        return safe
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    index = 1
    while True:
        candidate = f"{stem}（{index}）{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def upsert_license_doc(
    *,
    order_no: str,
    source: str,
    inspector_name: str,
    batch_date_value: date,
    pad_item_no: bool,
    summary: dict[str, Any],
    user_id: int | None,
    user_name: str,
) -> None:
    _ensure_tables()
    execute(
        """
        INSERT INTO license_docs (
            order_no, source, inspector_name, batch_date, pad_item_no,
            contract_amount, contract_weight, cs_amount, cs_weight, cs_unit_price,
            ss_amount, ss_weight, ss_unit_price, generated_by, generated_by_user_id, generated_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON DUPLICATE KEY UPDATE
            source=VALUES(source),
            inspector_name=VALUES(inspector_name),
            batch_date=VALUES(batch_date),
            pad_item_no=VALUES(pad_item_no),
            contract_amount=VALUES(contract_amount),
            contract_weight=VALUES(contract_weight),
            cs_amount=VALUES(cs_amount),
            cs_weight=VALUES(cs_weight),
            cs_unit_price=VALUES(cs_unit_price),
            ss_amount=VALUES(ss_amount),
            ss_weight=VALUES(ss_weight),
            ss_unit_price=VALUES(ss_unit_price),
            generated_by=VALUES(generated_by),
            generated_by_user_id=VALUES(generated_by_user_id),
            generated_at=NOW()
        """,
        (
            order_no,
            source,
            inspector_name,
            batch_date_value,
            1 if pad_item_no else 0,
            str(summary["contract_amount"]),
            str(summary["contract_weight"]),
            str(summary["cs_amount"]),
            str(summary["cs_weight"]),
            None if summary["cs_unit_price"] is None else str(summary["cs_unit_price"]),
            str(summary["ss_amount"]),
            str(summary["ss_weight"]),
            None if summary["ss_unit_price"] is None else str(summary["ss_unit_price"]),
            user_name,
            user_id,
        ),
    )


def build_license_zip(
    *,
    order_no: str,
    pad_item_no: bool,
    inspector_name: str,
    batch_date_value: date,
    contract_files: list[tuple[str, Path]],
    user_id: int | None,
    user_name: str,
) -> tuple[Path, list[str]]:
    loaded = load_license_rows(order_no)
    if not loaded["has_source"] and not contract_files:
        raise ValueError("订单号未出现在法国客户箱单或订单明细中")
    summary = summarize_rows(loaded["items"])
    digits = loaded["order_no"]
    work = Path(tempfile.mkdtemp(prefix="license_out_"))
    produced: list[tuple[str, Path]] = []
    used_names: set[str] = set()

    try:
        if summary["by_grade"][GRADE_A105]:
            name = OUTPUT_ATTACHMENT["CS"].format(order_no=digits)
            path = work / name
            _render_attachment(digits, "CS", summary["by_grade"][GRADE_A105], summary, pad_item_no, path)
            produced.append((name, path))
        ss_rows = summary["by_grade"][GRADE_304] + summary["by_grade"][GRADE_316]
        if ss_rows:
            name = OUTPUT_ATTACHMENT["SS"].format(order_no=digits)
            path = work / name
            _render_attachment(digits, "SS", ss_rows, summary, pad_item_no, path)
            produced.append((name, path))

        batch_text = format_batch_date(batch_date_value)
        for grade in KNOWN_GRADES:
            rows = summary["by_grade"][grade]
            if not rows:
                continue
            name = OUTPUT_CERTIFICATE[grade].format(order_no=digits)
            path = work / name
            _render_certificate(digits, grade, rows, inspector_name, batch_text, path)
            produced.append((name, path))

        for file_name, src in contract_files:
            if not src.is_file():
                raise FileNotFoundError(f"合同归档文件不存在：{file_name}")
            arc = _unique_arcname(used_names, file_name)
            dest = work / arc
            shutil.copyfile(src, dest)
            produced.append((arc, dest))

        if not produced:
            raise ValueError("没有可生成的许可证文件：请勾选客户合同，或确认订单含 A105/304/316")

        upsert_license_doc(
            order_no=digits,
            source=loaded["source"],
            inspector_name=inspector_name,
            batch_date_value=batch_date_value,
            pad_item_no=pad_item_no,
            summary=summary,
            user_id=user_id,
            user_name=user_name,
        )

        fd, zip_name = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        zip_path = Path(zip_name)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, path in produced:
                zf.write(path, arcname=name)
        return zip_path, [name for name, _path in produced]
    finally:
        shutil.rmtree(work, ignore_errors=True)
