"""仓库模块演示数据：保证各页面/Tab 至少有 1–3 条可视记录。

用法（在 backend 目录）:
  python scripts/seed_warehouse_demo.py

可重复执行：已存在的编码会跳过建档，业务单据按 DEMO 标记追加（若本月 DEMO 采购已满 3 条则跳过）。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.api.deps import CurrentUser
from app.api.v1 import warehouse as wh
from app.core.database import fetch_all, fetch_one


def _user() -> CurrentUser:
    row = fetch_one(
        """
        SELECT u.id, u.name, u.username, u.role_id, u.department, u.language_preference, u.layout_mode, u.color_theme,
               r.code AS role_code, r.name_zh AS role_name_zh
        FROM users u
        LEFT JOIN roles r ON r.id = u.role_id
        WHERE u.username='admin' OR u.id=1
        ORDER BY u.id LIMIT 1
        """
    )
    if not row:
        raise RuntimeError("未找到可用用户（admin），无法写入审计字段")
    return CurrentUser(
        id=int(row["id"]),
        name=str(row["name"] or "系统管理员"),
        username=str(row["username"]),
        role_id=int(row["role_id"] or 1),
        role_code=str(row.get("role_code") or "admin"),
        role_name_zh=str(row.get("role_name_zh") or "管理员"),
        department=row.get("department"),
        language_preference=str(row.get("language_preference") or "zh-CN"),
        layout_mode=str(row.get("layout_mode") or "horizontal"),
        color_theme=str(row.get("color_theme") or "classic-blue"),
    )


def _ensure_item(user: CurrentUser, code: str, **kwargs) -> int:
    exist = fetch_one("SELECT id FROM wh_aux_items WHERE item_code=%s", (code,))
    if exist:
        return int(exist["id"])
    res = wh.create_item(
        wh.AuxItemCreate(item_code=code, **kwargs),
        user,
    )
    return int(res["id"])


def _ensure_fg_item(user: CurrentUser, code: str, **kwargs) -> int:
    exist = fetch_one("SELECT id FROM wh_fg_items WHERE item_code=%s", (code,))
    if exist:
        return int(exist["id"])
    res = wh.create_fg_item(wh.FgItemCreate(item_code=code, **kwargs), user)
    return int(res["item"]["id"])


def _count(sql: str, params=()) -> int:
    row = fetch_one(sql, params)
    return int(row["c"] if row else 0)


def main() -> None:
    user = _user()
    today = date.today()
    # 保证演示单据都落在当月，便于对账/月度汇总可见
    month_day = max(1, min(today.day, 28))
    d1 = today.replace(day=max(1, month_day - 2))
    d2 = today.replace(day=max(1, month_day - 5)) if month_day > 5 else today.replace(day=1)
    d3 = today.replace(day=max(1, month_day - 8)) if month_day > 8 else today.replace(day=1)
    print(f"[seed] operator={user.username} ({user.name})")

    # --- 辅料主数据（库存台账）---
    glove = _ensure_item(
        user,
        "AUX-DEMO-GLOVE",
        name_spec="线手套（演示）",
        uom="双",
        category="劳保",
        default_location_id=1,
        default_unit_price=3.5,
        qty_min=50,
        qty_max=500,
        qty_on_hand=30,  # 低于下限 → 预警
        remark="演示低库存",
    )
    oil = _ensure_item(
        user,
        "AUX-DEMO-OIL",
        name_spec="切削液（演示）",
        uom="桶",
        category="油品",
        default_location_id=2,
        default_unit_price=180,
        qty_min=5,
        qty_max=40,
        qty_on_hand=12,
    )
    blade = _ensure_item(
        user,
        "AUX-DEMO-BLADE",
        name_spec="锯片 400mm（演示）",
        uom="片",
        category="刀具",
        default_location_id=3,
        default_unit_price=95,
        qty_min=3,
        qty_max=20,
        qty_on_hand=8,
    )
    # 保持低库存，供预警面板/月度预警展示（入库后也不再补货）
    low = _ensure_item(
        user,
        "AUX-DEMO-LOW",
        name_spec="焊条（演示低库存）",
        uom="kg",
        category="焊材",
        default_location_id=1,
        default_unit_price=12,
        qty_min=20,
        qty_max=100,
        qty_on_hand=5,
        remark="DEMO-低库存预警",
    )
    wh._refresh_item_alerts(low, user)
    print(f"[seed] aux items ok: glove={glove}, oil={oil}, blade={blade}, low={low}")

    # --- 采购（月结 + 现金）---
    demo_po = _count(
        "SELECT COUNT(*) c FROM wh_purchase_orders WHERE remark LIKE %s OR summary LIKE %s",
        ("%DEMO%", "%演示%"),
    )
    if demo_po < 3:
        wh.create_purchase(
            wh.PurchaseCreate(
                pay_type="monthly",
                biz_date=d1,
                supplier_name="鑫淼",
                purchaser_name=user.name,
                remark="DEMO-月结采购1",
                lines=[
                    wh.PurchaseLine(item_id=glove, name_spec="线手套（演示）", uom="双", qty=100, unit_price=3.5),
                    wh.PurchaseLine(item_id=oil, name_spec="切削液（演示）", uom="桶", qty=5, unit_price=180),
                ],
            ),
            user,
        )
        wh.create_purchase(
            wh.PurchaseCreate(
                pay_type="monthly",
                biz_date=d2,
                supplier_name="新维",
                purchaser_name=user.name,
                remark="DEMO-月结采购2",
                lines=[wh.PurchaseLine(item_id=blade, name_spec="锯片 400mm（演示）", uom="片", qty=10, unit_price=95)],
            ),
            user,
        )
        cash = wh.create_purchase(
            wh.PurchaseCreate(
                pay_type="cash",
                biz_date=d1,
                supplier_name="人本",
                purchaser_name=user.name,
                remark="DEMO-现金采购",
                lines=[wh.PurchaseLine(item_id=glove, name_spec="线手套（演示）", uom="双", qty=20, unit_price=4.0)],
            ),
            user,
        )
        # 推进现金采购到 paid，写入现金账本
        po_id = int(cash["item"]["id"])
        for to in ("pending_verify", "pending_approve", "pending_pay", "paid"):
            wh.advance_cash_purchase(po_id, wh.CashStatusUpdate(to_status=to, comment="DEMO"), user)
        print("[seed] purchases + cash advance ok")
    else:
        print("[seed] purchases already seeded, skip")

    # --- 入库 ---
    demo_in = _count("SELECT COUNT(*) c FROM wh_inbound_orders WHERE remark LIKE %s", ("%DEMO%",))
    if demo_in < 2:
        wh.create_inbound(
            wh.InboundCreate(
                biz_date=d1,
                source_type="purchase",
                supplier_name="鑫淼",
                operator_name=user.name,
                remark="DEMO-入库-鑫淼",
                lines=[
                    wh.InboundLine(item_id=glove, location_id=1, qty=80, unit_price=3.5),
                    wh.InboundLine(item_id=oil, location_id=2, qty=5, unit_price=180),
                ],
            ),
            user,
        )
        wh.create_inbound(
            wh.InboundCreate(
                biz_date=d2,
                source_type="purchase",
                supplier_name="新维",
                operator_name=user.name,
                remark="DEMO-入库-新维",
                lines=[wh.InboundLine(item_id=blade, location_id=3, qty=10, unit_price=95)],
            ),
            user,
        )
        wh.create_inbound(
            wh.InboundCreate(
                biz_date=today,
                source_type="manual",
                supplier_name="宏达",
                operator_name=user.name,
                remark="DEMO-手工入库",
                lines=[wh.InboundLine(item_id=oil, location_id=2, qty=2, unit_price=180)],
            ),
            user,
        )
        print("[seed] inbound ok")
    else:
        print("[seed] inbound already seeded, skip")

    # --- 领用 ---
    demo_is = _count("SELECT COUNT(*) c FROM wh_issue_orders WHERE summary LIKE %s OR issue_no LIKE %s", ("%演示%", "%IS%"))
    # 用 remark 不好查；改看接收人含 DEMO
    demo_is2 = _count("SELECT COUNT(*) c FROM wh_issue_orders WHERE receiver_name LIKE %s", ("%DEMO%",))
    if demo_is2 < 2:
        wh.create_issue(
            wh.IssueCreate(
                biz_date=today,
                cost_center_code="machining_1",
                receiver_name="DEMO-张三",
                keeper_name=user.name,
                remark="DEMO-领用1",
                lines=[wh.IssueLine(item_id=glove, location_id=1, qty=10)],
            ),
            user,
        )
        wh.create_issue(
            wh.IssueCreate(
                biz_date=d1,
                cost_center_code="hammer_1",
                receiver_name="DEMO-李四",
                keeper_name=user.name,
                remark="DEMO-领用2",
                lines=[wh.IssueLine(item_id=oil, location_id=2, qty=1)],
            ),
            user,
        )
        wh.create_issue(
            wh.IssueCreate(
                biz_date=d2,
                cost_center_code="cutting",
                receiver_name="DEMO-王五",
                keeper_name=user.name,
                remark="DEMO-领用3",
                lines=[wh.IssueLine(item_id=blade, location_id=3, qty=2)],
            ),
            user,
        )
        print("[seed] issues ok")
    else:
        print("[seed] issues already seeded, skip")

    # --- 借用（借用中 / 已归还 / 逾期）---
    demo_br = _count("SELECT COUNT(*) c FROM wh_borrow_records WHERE purpose LIKE %s OR item_name_spec LIKE %s", ("%DEMO%", "%演示%"))
    if demo_br < 3:
        wh.create_borrow(
            wh.BorrowCreate(
                item_name_spec="万用表（演示）",
                qty=1,
                department="机加一车间",
                borrow_date=d1,
                due_date=today + timedelta(days=7),
                borrower_name="DEMO-赵六",
                keeper_name=user.name,
                purpose="DEMO-正常借用",
            ),
            user,
        )
        ret = wh.create_borrow(
            wh.BorrowCreate(
                item_name_spec="游标卡尺（演示）",
                qty=1,
                department="化验室",
                borrow_date=d3,
                due_date=d1,
                borrower_name="DEMO-钱七",
                keeper_name=user.name,
                purpose="DEMO-已归还",
            ),
            user,
        )
        wh.return_borrow(int(ret["item"]["id"]), user)
        wh.create_borrow(
            wh.BorrowCreate(
                item_name_spec="电钻（演示）",
                qty=1,
                department="包装车间",
                borrow_date=d3,
                due_date=d2,  # 已过期 → overdue
                borrower_name="DEMO-孙八",
                keeper_name=user.name,
                purpose="DEMO-逾期借用",
            ),
            user,
        )
        print("[seed] borrows ok")
    else:
        print("[seed] borrows already seeded, skip")

    # --- 现金账本补充转入 ---
    demo_cash = _count("SELECT COUNT(*) c FROM wh_cash_ledger WHERE remark LIKE %s", ("%DEMO%",))
    if demo_cash < 2:
        wh.create_cash_ledger(
            wh.CashLedgerCreate(biz_date=d3, transfer_in=5000, spent=0, remark="DEMO-备用金转入"),
            user,
        )
        wh.create_cash_ledger(
            wh.CashLedgerCreate(biz_date=d2, transfer_in=0, spent=120, remark="DEMO-零星支出"),
            user,
        )
        print("[seed] cash ledger ok")
    else:
        print("[seed] cash ledger already seeded, skip")

    # --- 成品 ---
    fg1 = _ensure_fg_item(user, "FG-DEMO-001", name_spec="法兰盘 DN100（演示）", material_grade="20#", uom="件")
    fg2 = _ensure_fg_item(user, "FG-DEMO-002", name_spec="轴套 Φ80（演示）", material_grade="45#", uom="件")
    fg3 = _ensure_fg_item(user, "FG-DEMO-003", name_spec="齿轮坯（演示）", material_grade="42CrMo", uom="件")
    wh_plant = fetch_one("SELECT id FROM wh_fg_warehouses WHERE code='plant'")
    wh_blank = fetch_one("SELECT id FROM wh_fg_warehouses WHERE code='blank'")
    plant_id = int(wh_plant["id"])
    blank_id = int(wh_blank["id"])

    demo_fg_txn = _count("SELECT COUNT(*) c FROM wh_fg_txns WHERE remark LIKE %s", ("%DEMO%",))
    if demo_fg_txn < 3:
        wh.create_fg_txn(
            wh.FgTxnCreate(
                biz_date=d2,
                txn_type="inbound_finish",
                warehouse_id=plant_id,
                item_id=fg1,
                heat_no="H20260301",
                order_no="SO-DEMO-01",
                location_name="A区-01",
                qty=20,
                operator_name=user.name,
                remark="DEMO-完工入库",
            ),
            user,
        )
        wh.create_fg_txn(
            wh.FgTxnCreate(
                biz_date=d1,
                txn_type="outbound_ship",
                warehouse_id=plant_id,
                item_id=fg1,
                heat_no="H20260301",
                order_no="SO-DEMO-01",
                qty=5,
                operator_name=user.name,
                remark="DEMO-发货出库",
            ),
            user,
        )
        # 低库存预警：数量 < 5
        wh.create_fg_txn(
            wh.FgTxnCreate(
                biz_date=today,
                txn_type="inbound_finish",
                warehouse_id=blank_id,
                item_id=fg2,
                heat_no="H20260310",
                order_no="SO-DEMO-02",
                location_name="毛坯架",
                qty=3,
                operator_name=user.name,
                remark="DEMO-低库存毛坯",
            ),
            user,
        )
        wh.create_fg_txn(
            wh.FgTxnCreate(
                biz_date=today,
                txn_type="inbound_finish",
                warehouse_id=plant_id,
                item_id=fg3,
                heat_no="H20260312",
                order_no="",
                location_name="B区-02",
                qty=12,
                operator_name=user.name,
                remark="DEMO-自备入库",
            ),
            user,
        )
        print("[seed] fg txns ok")
    else:
        print("[seed] fg txns already seeded, skip")

    # --- 汇总打印 ---
    checks = {
        "辅料台账": _count("SELECT COUNT(*) c FROM wh_aux_items WHERE is_active=1"),
        "采购单": _count("SELECT COUNT(*) c FROM wh_purchase_orders"),
        "入库单": _count("SELECT COUNT(*) c FROM wh_inbound_orders"),
        "领用单": _count("SELECT COUNT(*) c FROM wh_issue_orders"),
        "借用": _count("SELECT COUNT(*) c FROM wh_borrow_records"),
        "现金账本": _count("SELECT COUNT(*) c FROM wh_cash_ledger"),
        "辅料预警(未解决)": _count("SELECT COUNT(*) c FROM wh_alerts WHERE is_resolved=0"),
        "成品物料": _count("SELECT COUNT(*) c FROM wh_fg_items"),
        "成品余额": _count("SELECT COUNT(*) c FROM wh_fg_balances"),
        "成品流水": _count("SELECT COUNT(*) c FROM wh_fg_txns"),
        "成品偏低预警": _count("SELECT COUNT(*) c FROM wh_fg_balances WHERE qty_on_hand < 5"),
    }
    print("\n=== 可视数据条数 ===")
    for k, v in checks.items():
        flag = "OK" if v >= 1 else "EMPTY"
        print(f"  [{flag}] {k}: {v}")

    # 对账/月度依赖当月采购入库
    month = today.strftime("%Y-%m")
    rec = wh.list_reconcile(user, month=month)
    summ = wh.monthly_summary(user, month=month)
    alerts = wh.list_alerts(user)
    print(f"  [OK] 对账汇总行: {len(rec.get('items') or [])} ({month})")
    print(f"  [OK] 月度三单行: {len((summ or {}).get('triple_check') or [])}")
    print(f"  [OK] 站内预警: {len(alerts.get('items') or [])}")
    print("[seed] done")


if __name__ == "__main__":
    main()
