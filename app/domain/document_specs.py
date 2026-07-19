"""单据字段与模板冻结配置(v1.0)。"""
from typing import Dict, List

DOCUMENT_TYPES = ("quality_certificate", "packing_list", "shipping_mark")

DOCUMENT_LABELS: Dict[str, Dict[str, str]] = {
    "quality_certificate": {
        "zh-CN": "质保书",
        "en-US": "Quality Certificate",
        "ja-JP": "品質保証書",
    },
    "packing_list": {
        "zh-CN": "装箱单",
        "en-US": "Packing List",
        "ja-JP": "梱包明細書",
    },
    "shipping_mark": {
        "zh-CN": "唛头",
        "en-US": "Shipping Mark",
        "ja-JP": "荷印",
    },
}

# 今早交付v1.0冻结字段：先保障字段齐全和导出可用
FROZEN_FIELDS_V1: Dict[str, List[str]] = {
    "quality_certificate": [
        "document_no",
        "issue_date",
        "client_name",
        "contract_no",
        "order_no",
        "product_name",
        "specification",
        "material",
        "standard",
        "quantity",
        "inspector",
        "result",
        "remark",
    ],
    "packing_list": [
        "document_no",
        "issue_date",
        "client_name",
        "order_no",
        "box_no",
        "total_boxes",
        "product_name",
        "specification",
        "quantity",
        "gross_weight",
        "net_weight",
        "destination_port",
        "remark",
    ],
    "shipping_mark": [
        "document_no",
        "issue_date",
        "client_name",
        "order_no",
        "box_no",
        "destination_port",
        "mark_line_1",
        "mark_line_2",
        "mark_line_3",
        "remark",
    ],
}

TEMPLATE_V1: Dict[str, Dict[str, str]] = {
    "quality_certificate": {
        "title": "{doc_label}",
        "body": (
            "单据编号: {document_no}\n"
            "日期: {issue_date}\n"
            "客户: {client_name}\n"
            "合同号: {contract_no}\n"
            "订单号: {order_no}\n"
            "产品: {product_name}\n"
            "规格: {specification}\n"
            "材质: {material}\n"
            "标准: {standard}\n"
            "数量: {quantity}\n"
            "检验员: {inspector}\n"
            "检验结果: {result}\n"
            "备注: {remark}\n"
        ),
    },
    "packing_list": {
        "title": "{doc_label}",
        "body": (
            "单据编号: {document_no}\n"
            "日期: {issue_date}\n"
            "客户: {client_name}\n"
            "订单号: {order_no}\n"
            "箱号: {box_no}/{total_boxes}\n"
            "产品: {product_name}\n"
            "规格: {specification}\n"
            "数量: {quantity}\n"
            "毛重: {gross_weight}\n"
            "净重: {net_weight}\n"
            "目的港: {destination_port}\n"
            "备注: {remark}\n"
        ),
    },
    "shipping_mark": {
        "title": "{doc_label}",
        "body": (
            "{mark_line_1}\n"
            "{mark_line_2}\n"
            "{mark_line_3}\n"
            "CLIENT: {client_name}\n"
            "ORDER: {order_no}\n"
            "BOX: {box_no}\n"
            "PORT: {destination_port}\n"
            "NOTE: {remark}\n"
        ),
    },
}


def get_document_label(document_type: str, lang: str) -> str:
    labels = DOCUMENT_LABELS.get(document_type, {})
    return labels.get(lang) or labels.get("zh-CN") or document_type
