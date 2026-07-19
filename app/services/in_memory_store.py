"""MVP阶段内存存储，便于快速联调验证。"""
from datetime import datetime
from itertools import count
from typing import Dict, List

document_job_seq = count(1)
contract_seq = count(1001)
order_seq = count(5001)
payment_seq = count(1)
order_detail_seq = count(1)
packing_detail_seq = count(1)
quality_record_seq = count(1)

document_jobs: Dict[int, dict] = {}

contracts: Dict[int, dict] = {}
production_orders: Dict[int, dict] = {}
production_events: List[dict] = []

payments: Dict[int, dict] = {}
reconciliations: Dict[int, dict] = {}

# 新增：订单明细、装箱明细、质检记录
order_details: List[dict] = []
packing_details: List[dict] = []
quality_records: Dict[int, dict] = {}
packing_sessions: Dict[str, dict] = {}
heat_treatment_test_records: Dict[str, dict] = {}
materials: List[dict] = []

ORDER_STATUSES = (
    "开始",
    "分割",
    "加工",
    "入库",
    "质检",
    "再加工",
    "包装",
    "装箱",
    "打包",
    "发货",
    "完成",
)
ORDER_UPLOAD_TYPES = ("文件上传", "手动录入")
MATERIAL_MODES = ("本厂钢材", "外来毛胚", "外来成品")


def now_iso() -> str:
    # MySQL DATETIME expects "YYYY-MM-DD HH:MM:SS", not ISO-8601 with trailing Z.
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_local_compact() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")
