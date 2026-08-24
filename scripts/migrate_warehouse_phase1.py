"""仓库模块 Phase1 建表 + 种子数据。

用法（在 backend 目录）:
  python scripts/migrate_warehouse_phase1.py
"""
from __future__ import annotations

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import execute, fetch_one
from app.services.warehouse_gap import GAP_TABLES

TABLES: list[tuple[str, str]] = [
    (
        "wh_operation_logs",
        """
        CREATE TABLE IF NOT EXISTS wh_operation_logs (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          domain VARCHAR(16) NOT NULL COMMENT 'aux|fg|common',
          action VARCHAR(32) NOT NULL,
          entity_type VARCHAR(64) NOT NULL,
          entity_id BIGINT NULL,
          entity_no VARCHAR(64) NULL,
          operator_user_id BIGINT NULL,
          operator_name VARCHAR(64) NOT NULL DEFAULT '',
          operated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          change_summary TEXT NULL,
          client_info VARCHAR(255) NULL,
          KEY idx_wh_log_entity (entity_type, entity_id),
          KEY idx_wh_log_action (action),
          KEY idx_wh_log_time (operated_at),
          KEY idx_wh_log_operator (operator_user_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库模块操作日志'
        """,
    ),
    (
        "wh_cost_centers",
        """
        CREATE TABLE IF NOT EXISTS wh_cost_centers (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          code VARCHAR(32) NOT NULL UNIQUE,
          name VARCHAR(64) NOT NULL,
          is_active TINYINT NOT NULL DEFAULT 1,
          sort_no INT NOT NULL DEFAULT 0,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成本中心字典'
        """,
    ),
    (
        "wh_suppliers",
        """
        CREATE TABLE IF NOT EXISTS wh_suppliers (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          name VARCHAR(100) NOT NULL,
          settle_type VARCHAR(20) NOT NULL DEFAULT 'monthly',
          remark VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库供应商'
        """,
    ),
    (
        "wh_aux_locations",
        """
        CREATE TABLE IF NOT EXISTS wh_aux_locations (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          code VARCHAR(32) NOT NULL UNIQUE,
          name VARCHAR(64) NOT NULL,
          sort_no INT NOT NULL DEFAULT 0,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料货架'
        """,
    ),
    (
        "wh_aux_items",
        """
        CREATE TABLE IF NOT EXISTS wh_aux_items (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          item_code VARCHAR(64) NOT NULL UNIQUE,
          name_spec VARCHAR(255) NOT NULL,
          uom VARCHAR(32) NOT NULL,
          uom_desc VARCHAR(64) NULL,
          category VARCHAR(64) NULL,
          default_location_id BIGINT NULL,
          default_unit_price DECIMAL(12,4) NULL,
          qty_min DECIMAL(14,3) NULL,
          qty_max DECIMAL(14,3) NULL,
          remark VARCHAR(255) NULL,
          is_active TINYINT NOT NULL DEFAULT 1,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库辅料主数据'
        """,
    ),
    (
        "wh_aux_balances",
        """
        CREATE TABLE IF NOT EXISTS wh_aux_balances (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          item_id BIGINT NOT NULL,
          location_id BIGINT NULL,
          qty_on_hand DECIMAL(14,3) NOT NULL DEFAULT 0,
          last_unit_price DECIMAL(12,4) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_item_loc (item_id, location_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料库存余额'
        """,
    ),
    (
        "wh_aux_txns",
        """
        CREATE TABLE IF NOT EXISTS wh_aux_txns (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          txn_no VARCHAR(32) NOT NULL,
          txn_type VARCHAR(32) NOT NULL,
          biz_date DATE NOT NULL,
          item_id BIGINT NOT NULL,
          location_id BIGINT NULL,
          qty DECIMAL(14,3) NOT NULL,
          unit_price DECIMAL(12,4) NULL,
          amount DECIMAL(14,2) NULL,
          cost_center_code VARCHAR(32) NULL,
          ref_type VARCHAR(32) NULL,
          ref_id BIGINT NULL,
          operator_name VARCHAR(64) NULL,
          keeper_name VARCHAR(64) NULL,
          remark VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_item_date (item_id, biz_date),
          KEY idx_txn_no (txn_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料出入库流水'
        """,
    ),
    (
        "wh_purchase_orders",
        """
        CREATE TABLE IF NOT EXISTS wh_purchase_orders (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          po_no VARCHAR(32) NOT NULL UNIQUE,
          pay_type VARCHAR(16) NOT NULL,
          supplier_id BIGINT NULL,
          supplier_name VARCHAR(100) NULL,
          biz_date DATE NOT NULL,
          status VARCHAR(32) NOT NULL,
          purchaser_user_id BIGINT NULL,
          purchaser_name VARCHAR(64) NULL,
          total_qty DECIMAL(14,3) NULL,
          total_amount DECIMAL(14,2) NULL,
          remark VARCHAR(255) NULL,
          summary VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购单'
        """,
    ),
    (
        "wh_purchase_order_lines",
        """
        CREATE TABLE IF NOT EXISTS wh_purchase_order_lines (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          po_id BIGINT NOT NULL,
          item_id BIGINT NULL,
          name_spec VARCHAR(255) NOT NULL,
          uom VARCHAR(32) NOT NULL,
          qty DECIMAL(14,3) NOT NULL,
          unit_price DECIMAL(12,4) NOT NULL,
          amount DECIMAL(14,2) NOT NULL,
          line_no INT NOT NULL DEFAULT 1,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_po_id (po_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='采购明细'
        """,
    ),
    (
        "wh_inbound_orders",
        """
        CREATE TABLE IF NOT EXISTS wh_inbound_orders (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          inbound_no VARCHAR(32) NOT NULL UNIQUE,
          biz_date DATE NOT NULL,
          source_type VARCHAR(32) NOT NULL,
          supplier_id BIGINT NULL,
          supplier_name VARCHAR(100) NULL,
          ref_po_id BIGINT NULL,
          ref_po_no VARCHAR(32) NULL,
          total_qty DECIMAL(14,3) NULL,
          total_amount DECIMAL(14,2) NULL,
          operator_name VARCHAR(64) NULL,
          remark VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料入库单'
        """,
    ),
    (
        "wh_inbound_order_lines",
        """
        CREATE TABLE IF NOT EXISTS wh_inbound_order_lines (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          inbound_id BIGINT NOT NULL,
          item_id BIGINT NOT NULL,
          location_id BIGINT NULL,
          uom VARCHAR(32) NOT NULL,
          qty DECIMAL(14,3) NOT NULL,
          unit_price DECIMAL(12,4) NULL,
          amount DECIMAL(14,2) NULL,
          line_no INT NOT NULL DEFAULT 1,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_inbound_id (inbound_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='辅料入库明细'
        """,
    ),
    (
        "wh_issue_orders",
        """
        CREATE TABLE IF NOT EXISTS wh_issue_orders (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          issue_no VARCHAR(32) NOT NULL UNIQUE,
          biz_date DATE NOT NULL,
          cost_center_code VARCHAR(32) NOT NULL,
          cost_center_name VARCHAR(64) NULL,
          receiver_name VARCHAR(64) NOT NULL,
          keeper_name VARCHAR(64) NULL,
          issue_mode VARCHAR(16) NOT NULL DEFAULT 'normal',
          total_qty DECIMAL(14,3) NULL,
          total_amount DECIMAL(14,2) NULL,
          summary VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='领用出库单'
        """,
    ),
    (
        "wh_issue_order_lines",
        """
        CREATE TABLE IF NOT EXISTS wh_issue_order_lines (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          issue_id BIGINT NOT NULL,
          item_id BIGINT NOT NULL,
          location_id BIGINT NULL,
          qty DECIMAL(14,3) NOT NULL,
          uom VARCHAR(32) NOT NULL,
          unit_price DECIMAL(12,4) NULL,
          amount DECIMAL(14,2) NULL,
          consumable_type VARCHAR(32) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_issue_id (issue_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='领用出库明细'
        """,
    ),
    (
        "wh_borrow_records",
        """
        CREATE TABLE IF NOT EXISTS wh_borrow_records (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          item_name_spec VARCHAR(255) NOT NULL,
          qty DECIMAL(14,3) NOT NULL,
          department VARCHAR(64) NULL,
          borrow_date DATE NOT NULL,
          due_date DATE NULL,
          return_date DATE NULL,
          borrower_name VARCHAR(64) NOT NULL,
          keeper_name VARCHAR(64) NULL,
          purpose VARCHAR(255) NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'borrowed',
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='借用登记'
        """,
    ),
    (
        "wh_cash_ledger",
        """
        CREATE TABLE IF NOT EXISTS wh_cash_ledger (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          biz_date DATE NOT NULL,
          transfer_in DECIMAL(14,2) NOT NULL DEFAULT 0,
          prev_balance DECIMAL(14,2) NOT NULL DEFAULT 0,
          spent DECIMAL(14,2) NOT NULL DEFAULT 0,
          next_balance DECIMAL(14,2) NOT NULL DEFAULT 0,
          remark VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现金备用金账本'
        """,
    ),
    (
        "wh_alerts",
        """
        CREATE TABLE IF NOT EXISTS wh_alerts (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          domain VARCHAR(16) NOT NULL,
          alert_type VARCHAR(32) NOT NULL,
          title VARCHAR(255) NOT NULL,
          level VARCHAR(16) NOT NULL DEFAULT 'warning',
          ref_type VARCHAR(32) NULL,
          ref_id BIGINT NULL,
          is_resolved TINYINT NOT NULL DEFAULT 0,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_wh_alert_open (domain, is_resolved)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='站内预警'
        """,
    ),
    (
        "wh_fg_warehouses",
        """
        CREATE TABLE IF NOT EXISTS wh_fg_warehouses (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          code VARCHAR(32) NOT NULL UNIQUE,
          name VARCHAR(64) NOT NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成品仓类型'
        """,
    ),
    (
        "wh_fg_items",
        """
        CREATE TABLE IF NOT EXISTS wh_fg_items (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          item_code VARCHAR(64) NOT NULL UNIQUE,
          name_spec VARCHAR(255) NOT NULL,
          material_grade VARCHAR(64) NULL,
          uom VARCHAR(32) NOT NULL DEFAULT '件',
          remark VARCHAR(255) NULL,
          is_active TINYINT NOT NULL DEFAULT 1,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='仓库物料(成品)'
        """,
    ),
    (
        "wh_fg_balances",
        """
        CREATE TABLE IF NOT EXISTS wh_fg_balances (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          warehouse_id BIGINT NOT NULL,
          item_id BIGINT NOT NULL,
          heat_no VARCHAR(64) NOT NULL DEFAULT '',
          order_no VARCHAR(64) NOT NULL DEFAULT '',
          location_name VARCHAR(64) NULL,
          qty_on_hand DECIMAL(14,3) NOT NULL DEFAULT 0,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          UNIQUE KEY uk_fg_bal (warehouse_id, item_id, heat_no, order_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成品库存余额'
        """,
    ),
    (
        "wh_fg_txns",
        """
        CREATE TABLE IF NOT EXISTS wh_fg_txns (
          id BIGINT PRIMARY KEY AUTO_INCREMENT,
          txn_no VARCHAR(32) NOT NULL,
          txn_type VARCHAR(32) NOT NULL,
          biz_date DATE NOT NULL,
          warehouse_id BIGINT NOT NULL,
          item_id BIGINT NOT NULL,
          heat_no VARCHAR(64) NULL,
          order_no VARCHAR(64) NULL,
          qty DECIMAL(14,3) NOT NULL,
          operator_name VARCHAR(64) NULL,
          remark VARCHAR(255) NULL,
          created_by_user_id BIGINT NULL,
          created_by_name VARCHAR(64) NOT NULL DEFAULT '',
          created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_by_user_id BIGINT NULL,
          updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
          updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          KEY idx_fg_txn_no (txn_no)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='成品出入库流水'
        """,
    ),
]

TABLES.extend(GAP_TABLES)


COST_CENTERS = [
    ("machining_1", "机加一车间", 10),
    ("machining_2", "机加二车间", 20),
    ("hammer_1", "1号锤", 30),
    ("hammer_3", "3号锤", 40),
    ("cutting", "下料车间", 50),
    ("packing", "包装车间", 60),
    ("lab", "化验室", 70),
    ("office", "办公室", 80),
    ("other", "其他", 90),
]

LOCATIONS = [
    ("A", "货架A", 10),
    ("B", "货架B", 20),
    ("C", "货架C", 30),
]

FG_WAREHOUSES = [
    ("plant", "本厂成品"),
    ("order_stock", "订单备库"),
    ("self_stock", "自备库"),
    ("blank", "库毛坯"),
]

SUPPLIERS = [
    ("鑫淼", "monthly"),
    ("新维", "monthly"),
    ("人本", "monthly"),
    ("宏达", "both"),
]


def table_exists(name: str) -> bool:
    row = fetch_one(
        """
        SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
        """,
        (name,),
    )
    return bool(row)


def migrate() -> None:
    for name, ddl in TABLES:
        if table_exists(name):
            print(f"[skip] {name} exists")
        else:
            execute(ddl)
            print(f"[ok] created {name}")

    for code, name, sort_no in COST_CENTERS:
        exists = fetch_one("SELECT id FROM wh_cost_centers WHERE code=%s", (code,))
        if exists:
            continue
        execute(
            """
            INSERT INTO wh_cost_centers
              (code, name, sort_no, created_by_name, updated_by_name)
            VALUES (%s, %s, %s, 'system', 'system')
            """,
            (code, name, sort_no),
        )
        print(f"[seed] cost_center {code}")

    for code, name, sort_no in LOCATIONS:
        exists = fetch_one("SELECT id FROM wh_aux_locations WHERE code=%s", (code,))
        if exists:
            continue
        execute(
            """
            INSERT INTO wh_aux_locations
              (code, name, sort_no, created_by_name, updated_by_name)
            VALUES (%s, %s, %s, 'system', 'system')
            """,
            (code, name, sort_no),
        )
        print(f"[seed] location {code}")

    for code, name in FG_WAREHOUSES:
        exists = fetch_one("SELECT id FROM wh_fg_warehouses WHERE code=%s", (code,))
        if exists:
            continue
        execute(
            """
            INSERT INTO wh_fg_warehouses
              (code, name, created_by_name, updated_by_name)
            VALUES (%s, %s, 'system', 'system')
            """,
            (code, name),
        )
        print(f"[seed] fg_warehouse {code}")

    for name, settle in SUPPLIERS:
        exists = fetch_one("SELECT id FROM wh_suppliers WHERE name=%s", (name,))
        if exists:
            continue
        execute(
            """
            INSERT INTO wh_suppliers
              (name, settle_type, created_by_name, updated_by_name)
            VALUES (%s, %s, 'system', 'system')
            """,
            (name, settle),
        )
        print(f"[seed] supplier ok")

    print("warehouse phase1 migrate done")


if __name__ == "__main__":
    migrate()
