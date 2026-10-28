"""一线表格处理软件同款二维码生成（Python qrcode 库，QR Code）。"""
from __future__ import annotations

import io

import qrcode

# 与 table_gui.generate_qr_code 一致；打印时只加大 box_size，避免拉伸发糊。
FIELD_ERROR_CORRECTION = qrcode.constants.ERROR_CORRECT_L
FIELD_VERSION = 1
FIELD_BORDER = 1
FIELD_BOX_SIZE = 4
PRINT_BOX_SIZE = 16


def _make_qr(text: str, box_size: int):
    qr = qrcode.QRCode(
        version=FIELD_VERSION,
        error_correction=FIELD_ERROR_CORRECTION,
        box_size=max(1, int(box_size)),
        border=FIELD_BORDER,
    )
    qr.add_data(text if text else " ")
    qr.make(fit=True)
    return qr


def encode_qr_matrix(text: str) -> list[list[int]]:
    """返回 1=黑、0=白 的模块点阵，供设计预览。"""
    qr = _make_qr(text, box_size=1)
    return [[1 if cell else 0 for cell in row] for row in qr.get_matrix()]


def make_qr_png(text: str, box_size: int = PRINT_BOX_SIZE) -> bytes:
    """生成黑白 PNG，参数与一线一致（除打印用更大 box_size）。"""
    qr = _make_qr(text, box_size=box_size)
    image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
