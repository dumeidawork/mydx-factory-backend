from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db


def has_column(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return cursor.fetchone() is not None


def has_table(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        LIMIT 1
        """,
        (table_name,),
    )
    return cursor.fetchone() is not None


def has_index(cursor, table_name: str, index_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
        LIMIT 1
        """,
        (table_name, index_name),
    )
    return cursor.fetchone() is not None


def main() -> None:
    create_statements = [
        "CREATE TABLE IF NOT EXISTS production_events (id INT PRIMARY KEY AUTO_INCREMENT, order_id INT NOT NULL, node VARCHAR(64) NOT NULL, operator VARCHAR(64), remark VARCHAR(255), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_prod_event_order (order_id))",
        "CREATE TABLE IF NOT EXISTS reconciliations (id INT PRIMARY KEY AUTO_INCREMENT, contract_no VARCHAR(64) NOT NULL, client_name VARCHAR(128) NOT NULL, amount DECIMAL(14,2) NOT NULL, currency VARCHAR(16) DEFAULT 'CNY', invoice_no VARCHAR(64), status VARCHAR(32) DEFAULT 'generated', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS heat_treatment_test_records (id BIGINT AUTO_INCREMENT PRIMARY KEY, heat_no VARCHAR(50) NOT NULL, batch_no VARCHAR(50) NOT NULL, material_no VARCHAR(128) NOT NULL DEFAULT '', mech_test_temp VARCHAR(50), mech_yield_strength VARCHAR(50), mech_tensile_strength VARCHAR(50), mech_elongation VARCHAR(50), mech_reduction_area VARCHAR(50), mech_hardness VARCHAR(50), mech_impact_test VARCHAR(100), chem_raw_c DECIMAL(8,4), chem_raw_mn DECIMAL(8,4), chem_raw_p DECIMAL(8,4), chem_raw_s DECIMAL(8,4), chem_raw_si DECIMAL(8,4), chem_raw_cu DECIMAL(8,4), chem_raw_ni DECIMAL(8,4), chem_raw_cr DECIMAL(8,4), chem_raw_mo DECIMAL(8,4), chem_raw_v DECIMAL(8,4), chem_raw_ceq DECIMAL(8,4), chem_self_c DECIMAL(8,4), chem_self_mn DECIMAL(8,4), chem_self_p DECIMAL(8,4), chem_self_s DECIMAL(8,4), chem_self_si DECIMAL(8,4), chem_self_cu DECIMAL(8,4), chem_self_ni DECIMAL(8,4), chem_self_cr DECIMAL(8,4), chem_self_mo DECIMAL(8,4), chem_self_v DECIMAL(8,4), chem_self_ceq DECIMAL(8,4), test_result VARCHAR(16), remark VARCHAR(255), created_by VARCHAR(50), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_heat_batch_material (heat_no, batch_no, material_no), INDEX idx_heat_no (heat_no), INDEX idx_batch_no (batch_no), INDEX idx_material_no (material_no))",
        "CREATE TABLE IF NOT EXISTS heat_treatment_trial_records (id BIGINT AUTO_INCREMENT PRIMARY KEY, test_date VARCHAR(8), heat_no VARCHAR(50) NOT NULL, batch_no VARCHAR(50) NOT NULL, spec_model VARCHAR(255) NOT NULL DEFAULT '', mech_yield_strength VARCHAR(50) NOT NULL, mech_tensile_strength VARCHAR(50) NOT NULL, mech_elongation VARCHAR(50) NOT NULL, mech_reduction_area VARCHAR(50) NOT NULL, mech_hardness_1 VARCHAR(50) NOT NULL, mech_hardness_2 VARCHAR(50) NOT NULL, mech_hardness_3 VARCHAR(50) NOT NULL, mech_impact_test VARCHAR(100) NOT NULL, seq_no VARCHAR(50), supplier_hardness VARCHAR(100), material_no VARCHAR(128) NOT NULL DEFAULT '', remark VARCHAR(255), material VARCHAR(64), updated_by VARCHAR(50) NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_trial_heat_batch (heat_no, batch_no), UNIQUE KEY uk_trial_heat_batch_material_spec (heat_no, batch_no, material_no, spec_model))",
        "CREATE TABLE IF NOT EXISTS heat_treatment_chemical_records (id BIGINT AUTO_INCREMENT PRIMARY KEY, heat_no VARCHAR(50) NOT NULL, chem_raw_c DECIMAL(8,4), chem_raw_mn DECIMAL(8,4), chem_raw_p DECIMAL(8,4), chem_raw_s DECIMAL(8,4), chem_raw_si DECIMAL(8,4), chem_raw_cu DECIMAL(8,4), chem_raw_ni DECIMAL(8,4), chem_raw_cr DECIMAL(8,4), chem_raw_mo DECIMAL(8,4), chem_raw_v DECIMAL(8,4), chem_raw_ceq DECIMAL(8,4), chem_self_c DECIMAL(8,4), chem_self_mn DECIMAL(8,4), chem_self_p DECIMAL(8,4), chem_self_s DECIMAL(8,4), chem_self_si DECIMAL(8,4), chem_self_cu DECIMAL(8,4), chem_self_ni DECIMAL(8,4), chem_self_cr DECIMAL(8,4), chem_self_mo DECIMAL(8,4), chem_self_v DECIMAL(8,4), chem_self_ceq DECIMAL(8,4), updated_by VARCHAR(50) NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_chemical_heat_no (heat_no))",
        "CREATE TABLE IF NOT EXISTS materials (id BIGINT AUTO_INCREMENT PRIMARY KEY, material_no VARCHAR(100) NOT NULL, part_no VARCHAR(100) NULL, drawing_no VARCHAR(255) NULL, spec_model VARCHAR(255) NULL, material VARCHAR(100) NULL, unit_weight DECIMAL(10,3) NOT NULL DEFAULT 0.000, remark TEXT NULL, created_by VARCHAR(50) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_by VARCHAR(50) NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_material_no (material_no), INDEX idx_part_no (part_no), INDEX idx_drawing_no (drawing_no))",
        "CREATE TABLE IF NOT EXISTS dalian_shipping_details (id BIGINT AUTO_INCREMENT PRIMARY KEY, delivery_date VARCHAR(8) NOT NULL, packing_no VARCHAR(50) NULL, order_no VARCHAR(100) NOT NULL, item_no VARCHAR(50) NOT NULL, drawing_no VARCHAR(255) NULL, ap1_material_no VARCHAR(100) NULL, r3p_material_no VARCHAR(100) NULL, spec_model VARCHAR(255) NULL, material VARCHAR(100) NULL, quantity INT DEFAULT 0, unit_weight DECIMAL(10,3) DEFAULT 0.000, total_weight DECIMAL(10,3) DEFAULT 0.000, unit_price DECIMAL(10,2) NULL, remark TEXT NULL, buyer VARCHAR(100) NULL, heat_no VARCHAR(100) NULL, heat_treatment_batch_no VARCHAR(100) NULL, shipping_mark_remark VARCHAR(255) NULL, created_by VARCHAR(50) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_delivery_date (delivery_date), INDEX idx_order_item (order_no, item_no)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS dalian_certificate_allocations (id INT PRIMARY KEY AUTO_INCREMENT, order_detail_id INT NULL, order_no VARCHAR(64) NOT NULL, material_no VARCHAR(128) NOT NULL, item_no_key INT NOT NULL DEFAULT 0, certificate_no VARCHAR(64) NOT NULL, cert_date_yymmdd VARCHAR(6) NOT NULL, cert_daily_seq INT NOT NULL, first_assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_dalian_cert_business (order_detail_id), INDEX idx_dalian_cert_order_material (order_no, material_no, item_no_key), INDEX idx_dalian_cert_date (cert_date_yymmdd)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS dalian_certificate_daily_counters (cert_date_yymmdd VARCHAR(6) PRIMARY KEY, next_seq INT NOT NULL DEFAULT 1) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS qc_certificate_snapshots (id INT PRIMARY KEY AUTO_INCREMENT, order_detail_id INT NULL, order_no VARCHAR(64) NOT NULL, material_no VARCHAR(128) NOT NULL, item_no INT NULL, item_no_key INT NOT NULL DEFAULT 0, heat_no VARCHAR(64) NOT NULL DEFAULT '', heat_treatment_batch_no VARCHAR(64) NOT NULL DEFAULT '', certificate_type VARCHAR(16) NOT NULL, base_info JSON, delivery_content JSON, mechanical_tests JSON, chemical_analysis JSON, latest_qc_record_id INT NULL, certificate_path VARCHAR(512), generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_qc_snapshot_business (order_detail_id, order_no, material_no, item_no_key, heat_no, heat_treatment_batch_no), INDEX idx_qc_snapshot_order (order_no), INDEX idx_qc_snapshot_material (material_no), INDEX idx_qc_snapshot_detail (order_detail_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    ]
    from app.services.order_detail_audit import CREATE_LOG_TABLE_SQL as ORDER_DETAIL_LOG_TABLE_SQL
    from app.services.price_split_schema import CREATE_LOG_TABLE_SQL, CREATE_TABLE_SQL
    from app.api.v1.customer_packing import CREATE_ITEM_SQL, CREATE_LIST_SQL, CREATE_LOG_SQL as FRANCE_PACKING_LOG_SQL, CREATE_ORDER_CONTRACT_SQL as FRANCE_ORDER_CONTRACT_SQL
    from app.api.v1.dalian_econtract import CREATE_ITEM_SQL as DALIAN_ECONTRACT_ITEM_SQL, CREATE_LIST_SQL as DALIAN_ECONTRACT_LIST_SQL, CREATE_LOG_SQL as DALIAN_ECONTRACT_LOG_SQL, CREATE_ORDER_CONTRACT_SQL as DALIAN_ORDER_CONTRACT_SQL
    from app.api.v1.paint_requirements import CREATE_LOG_TABLE_SQL as PAINT_LOG_TABLE_SQL, CREATE_TABLE_SQL as PAINT_TABLE_SQL
    from app.api.v1.dn_inch_maps import CREATE_LOG_TABLE_SQL as DN_INCH_LOG_TABLE_SQL, CREATE_TABLE_SQL as DN_INCH_TABLE_SQL
    from app.api.v1.drawing_flange_type_maps import CREATE_LOG_TABLE_SQL as DRAWING_FLANGE_LOG_TABLE_SQL, CREATE_TABLE_SQL as DRAWING_FLANGE_TABLE_SQL
    from app.api.v1.pallet_archives import ALTER_BOX_WEIGHT_SQL as PALLET_ARCHIVE_BOX_WEIGHT_SQL, CREATE_LOG_TABLE_SQL as PALLET_ARCHIVE_LOG_TABLE_SQL, CREATE_TABLE_SQL as PALLET_ARCHIVE_TABLE_SQL
    from app.api.v1.france_auto_packing_qty_maps import CREATE_LOG_TABLE_SQL as FRANCE_AUTO_PACK_QTY_LOG_SQL, CREATE_NOTICE_TABLE_SQL as FRANCE_AUTO_PACK_QTY_NOTICE_SQL, CREATE_TABLE_SQL as FRANCE_AUTO_PACK_QTY_TABLE_SQL, DALIAN_CREATE_LOG_TABLE_SQL, DALIAN_CREATE_TABLE_SQL
    from app.api.v1.france_auto_packing import CREATE_LOG_TABLE_SQL as FRANCE_AUTO_PACK_LOG_SQL
    from app.api.v1.dalian_packing import CREATE_LOG_TABLE_SQL as DALIAN_PACKING_LOG_SQL
    from app.api.v1.material_typing_maps import CREATE_LOG_TABLE_SQL as MATERIAL_TYPING_LOG_TABLE_SQL, CREATE_TABLE_SQL as MATERIAL_TYPING_TABLE_SQL
    from app.api.v1.typing_requirements import CREATE_LOG_TABLE_SQL as TYPING_LOG_TABLE_SQL, CREATE_TABLE_SQL as TYPING_TABLE_SQL
    from app.api.v1.qr_label_designs import CREATE_LOG_TABLE_SQL as QR_LABEL_LOG_TABLE_SQL, CREATE_TABLE_SQL as QR_LABEL_TABLE_SQL
    from app.services.license_schema import CREATE_LICENSE_CERTIFICATE_ITEMS_SQL, CREATE_LICENSE_DOCS_SQL
    from app.services.typing_notice import CREATE_TABLE_SQL as TYPING_NOTICE_TABLE_SQL
    from app.api.v1.dalian_shipping_reservations import (
        CREATE_DATE_SQL as DALIAN_SHIP_DATE_SQL,
        CREATE_LOG_SQL as DALIAN_SHIP_LOG_SQL,
        CREATE_RESERVATION_SQL as DALIAN_SHIP_RESERVATION_SQL,
        CREATE_UNREAD_SQL as DALIAN_SHIP_UNREAD_SQL,
    )

    create_statements.extend(
        [
            CREATE_TABLE_SQL["france"],
            CREATE_TABLE_SQL["dalian"],
            CREATE_LOG_TABLE_SQL,
            ORDER_DETAIL_LOG_TABLE_SQL,
            CREATE_LIST_SQL,
            CREATE_ITEM_SQL,
            FRANCE_PACKING_LOG_SQL,
            FRANCE_ORDER_CONTRACT_SQL,
            DALIAN_ECONTRACT_LIST_SQL,
            DALIAN_ECONTRACT_ITEM_SQL,
            DALIAN_ECONTRACT_LOG_SQL,
            DALIAN_ORDER_CONTRACT_SQL,
            CREATE_LICENSE_DOCS_SQL,
            CREATE_LICENSE_CERTIFICATE_ITEMS_SQL,
            PAINT_TABLE_SQL,
            PAINT_LOG_TABLE_SQL,
            DN_INCH_TABLE_SQL,
            DN_INCH_LOG_TABLE_SQL,
            DRAWING_FLANGE_TABLE_SQL,
            DRAWING_FLANGE_LOG_TABLE_SQL,
            PALLET_ARCHIVE_TABLE_SQL,
            PALLET_ARCHIVE_LOG_TABLE_SQL,
            FRANCE_AUTO_PACK_QTY_TABLE_SQL,
            FRANCE_AUTO_PACK_QTY_LOG_SQL,
            FRANCE_AUTO_PACK_QTY_NOTICE_SQL,
            DALIAN_CREATE_TABLE_SQL,
            DALIAN_CREATE_LOG_TABLE_SQL,
            FRANCE_AUTO_PACK_LOG_SQL,
            DALIAN_PACKING_LOG_SQL,
            MATERIAL_TYPING_TABLE_SQL,
            MATERIAL_TYPING_LOG_TABLE_SQL,
            TYPING_TABLE_SQL,
            TYPING_LOG_TABLE_SQL,
            TYPING_NOTICE_TABLE_SQL,
            QR_LABEL_TABLE_SQL,
            QR_LABEL_LOG_TABLE_SQL,
            DALIAN_SHIP_RESERVATION_SQL,
            DALIAN_SHIP_DATE_SQL,
            DALIAN_SHIP_UNREAD_SQL,
            DALIAN_SHIP_LOG_SQL,
        ]
    )
    alter_statements = [
        ("users", "layout_mode", "ALTER TABLE users ADD COLUMN layout_mode VARCHAR(16) NOT NULL DEFAULT 'horizontal' COMMENT 'horizontal, vertical, classic, mix, columns, overview' AFTER language_preference"),
        ("users", "color_theme", "ALTER TABLE users ADD COLUMN color_theme VARCHAR(32) NOT NULL DEFAULT 'classic-blue' COMMENT 'user color theme id' AFTER layout_mode"),
        ("document_jobs", "file_name", "ALTER TABLE document_jobs ADD COLUMN file_name VARCHAR(255) NULL AFTER status"),
        ("order_details", "packed_at", "ALTER TABLE order_details ADD COLUMN packed_at DATETIME NULL AFTER finished_at"),
        ("order_details", "shipped_at", "ALTER TABLE order_details ADD COLUMN shipped_at DATETIME NULL AFTER packed_at"),
        ("order_details", "split_group_id", "ALTER TABLE order_details ADD COLUMN split_group_id INT NULL COMMENT '部分发货拆分组，首次拆分时为原明细id' AFTER shipped_at"),
        ("order_details", "contract_qty", "ALTER TABLE order_details ADD COLUMN contract_qty INT NULL COMMENT '拆分前合同数量，同组各行相同' AFTER split_group_id"),
        ("order_details", "factory_order_no", "ALTER TABLE order_details ADD COLUMN factory_order_no VARCHAR(128) NULL AFTER customer"),
        ("order_details", "item_no", "ALTER TABLE order_details ADD COLUMN item_no INT NULL AFTER seq"),
        ("order_details", "agreement_price", "ALTER TABLE order_details ADD COLUMN agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '协议价' AFTER total_weight"),
        ("order_details", "product_unit_price", "ALTER TABLE order_details ADD COLUMN product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价' AFTER agreement_price"),
        ("order_details", "created_by", "ALTER TABLE order_details ADD COLUMN created_by VARCHAR(64) NULL COMMENT '上传者' AFTER updated_at"),
        ("order_details", "created_by_user_id", "ALTER TABLE order_details ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
        ("order_details", "updated_by", "ALTER TABLE order_details ADD COLUMN updated_by VARCHAR(64) NULL COMMENT '更改者' AFTER created_by_user_id"),
        ("order_details", "updated_by_user_id", "ALTER TABLE order_details ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
        ("packing_details", "delivery_date", "ALTER TABLE packing_details ADD COLUMN delivery_date DATE NULL AFTER packing_remark"),
        ("qc_records", "certificate_path", "ALTER TABLE qc_records ADD COLUMN certificate_path VARCHAR(512) NULL AFTER chemical_analysis"),
        ("heat_treatment_test_records", "test_result", "ALTER TABLE heat_treatment_test_records ADD COLUMN test_result VARCHAR(16) NULL AFTER chem_self_ceq"),
        ("heat_treatment_test_records", "remark", "ALTER TABLE heat_treatment_test_records ADD COLUMN remark VARCHAR(255) NULL AFTER test_result"),
        ("heat_treatment_test_records", "material_no", "ALTER TABLE heat_treatment_test_records ADD COLUMN material_no VARCHAR(128) NOT NULL DEFAULT '' AFTER batch_no"),
        ("materials", "ut", "ALTER TABLE materials ADD COLUMN ut VARCHAR(16) NULL COMMENT 'UT检测' AFTER remark"),
        ("materials", "mt", "ALTER TABLE materials ADD COLUMN mt VARCHAR(16) NULL COMMENT 'MT检测' AFTER ut"),
        ("materials", "pt", "ALTER TABLE materials ADD COLUMN pt VARCHAR(16) NULL COMMENT 'PT检测' AFTER mt"),
        ("materials", "product_unit_price", "ALTER TABLE materials ADD COLUMN product_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '产品单价' AFTER pt"),
        ("materials", "france_agreement_price", "ALTER TABLE materials ADD COLUMN france_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '法国协议价' AFTER product_unit_price"),
        ("materials", "dalian_agreement_price", "ALTER TABLE materials ADD COLUMN dalian_agreement_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '大连协议价' AFTER france_agreement_price"),
        ("materials", "drawing_archive_id", "ALTER TABLE materials ADD COLUMN drawing_archive_id BIGINT NULL COMMENT '对应图纸建档 id' AFTER drawing_no"),
        ("france_customer_packing_list_items", "box_no", "ALTER TABLE france_customer_packing_list_items ADD COLUMN box_no INT NULL COMMENT '主箱号（件数最多的箱）' AFTER match_warning"),
        ("france_customer_packing_list_items", "actual_delivery_date", "ALTER TABLE france_customer_packing_list_items ADD COLUMN actual_delivery_date DATE NULL COMMENT '实际交货日期（与离港日分开）' AFTER box_no"),
        ("packing_details", "created_by", "ALTER TABLE packing_details ADD COLUMN created_by VARCHAR(64) NULL COMMENT '上传者' AFTER updated_at"),
        ("packing_details", "created_by_user_id", "ALTER TABLE packing_details ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
        ("packing_details", "updated_by", "ALTER TABLE packing_details ADD COLUMN updated_by VARCHAR(64) NULL COMMENT '更改者' AFTER created_by_user_id"),
        ("packing_details", "updated_by_user_id", "ALTER TABLE packing_details ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
        ("dalian_shipping_details", "factory_order_no", "ALTER TABLE dalian_shipping_details ADD COLUMN factory_order_no VARCHAR(128) NULL COMMENT '本厂单号' AFTER order_no"),
        ("dalian_shipping_details", "created_by_user_id", "ALTER TABLE dalian_shipping_details ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
        ("dalian_shipping_details", "updated_by", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_by VARCHAR(64) NULL COMMENT '更改者' AFTER created_by_user_id"),
        ("dalian_shipping_details", "updated_by_user_id", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
        ("dalian_shipping_details", "updated_at", "ALTER TABLE dalian_shipping_details ADD COLUMN updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间' AFTER updated_by_user_id"),
        ("dalian_shipping_details", "reservation_id", "ALTER TABLE dalian_shipping_details ADD COLUMN reservation_id BIGINT NULL COMMENT '发货预定行ID，手工粘贴行为空' AFTER id"),
        ("dalian_shipping_details", "planned_arrival_date", "ALTER TABLE dalian_shipping_details ADD COLUMN planned_arrival_date DATE NULL COMMENT '预定到货日期' AFTER reservation_id"),
        ("dalian_shipping_reservations", "item_no", "ALTER TABLE dalian_shipping_reservations ADD COLUMN item_no INT NULL COMMENT '条目，来自订单明细，箱单显示' AFTER material"),
        ("dalian_shipping_reservations", "drawing_no", "ALTER TABLE dalian_shipping_reservations ADD COLUMN drawing_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号，来自订单明细，箱单显示' AFTER item_no"),
        ("dalian_shipping_reservations", "unit_weight", "ALTER TABLE dalian_shipping_reservations ADD COLUMN unit_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '单重，来自订单明细，箱单显示' AFTER drawing_no"),
        ("dalian_shipping_reservations", "total_weight", "ALTER TABLE dalian_shipping_reservations ADD COLUMN total_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '总重=单重×预定数量，箱单显示' AFTER unit_weight"),
        ("dalian_shipping_date_assignments", "packing_generated_by", "ALTER TABLE dalian_shipping_date_assignments ADD COLUMN packing_generated_by VARCHAR(64) NULL COMMENT '箱单最后生成者' AFTER assigned_by_name"),
        ("dalian_shipping_date_assignments", "packing_generated_by_user_id", "ALTER TABLE dalian_shipping_date_assignments ADD COLUMN packing_generated_by_user_id INT NULL COMMENT '箱单最后生成者用户ID' AFTER packing_generated_by"),
        ("dalian_shipping_date_assignments", "packing_generated_at", "ALTER TABLE dalian_shipping_date_assignments ADD COLUMN packing_generated_at DATETIME NULL COMMENT '箱单最后生成时间' AFTER packing_generated_by_user_id"),
        ("dalian_shipping_date_assignments", "packing_content_stale", "ALTER TABLE dalian_shipping_date_assignments ADD COLUMN packing_content_stale TINYINT NOT NULL DEFAULT 0 COMMENT '生成后预定内容已变，须重新生成才能开始装箱' AFTER packing_generated_at"),
        ("qc_records", "order_detail_id", "ALTER TABLE qc_records ADD COLUMN order_detail_id INT NULL AFTER certificate_path"),
        ("qc_records", "item_no", "ALTER TABLE qc_records ADD COLUMN item_no INT NULL AFTER order_detail_id"),
        ("qc_records", "item_no_key", "ALTER TABLE qc_records ADD COLUMN item_no_key INT NOT NULL DEFAULT 0 AFTER item_no"),
        ("qc_records", "cert_date_yymmdd", "ALTER TABLE qc_records ADD COLUMN cert_date_yymmdd VARCHAR(6) NULL AFTER item_no_key"),
        ("qc_records", "cert_daily_seq", "ALTER TABLE qc_records ADD COLUMN cert_daily_seq INT NULL AFTER cert_date_yymmdd"),
        ("qc_records", "certificate_type", "ALTER TABLE qc_records ADD COLUMN certificate_type VARCHAR(16) NULL AFTER cert_daily_seq"),
        ("dalian_certificate_allocations", "order_detail_id", "ALTER TABLE dalian_certificate_allocations ADD COLUMN order_detail_id INT NULL COMMENT '订单明细ID' AFTER id"),
        ("customer_contract_archives", "progress_status", "ALTER TABLE customer_contract_archives ADD COLUMN progress_status VARCHAR(16) NOT NULL DEFAULT '上传' COMMENT '合同进度：上传/分配/确认' AFTER owner_name"),
        ("customer_contract_archives", "assigned_at", "ALTER TABLE customer_contract_archives ADD COLUMN assigned_at DATETIME NULL COMMENT '进入本次分配的时间' AFTER progress_status"),
        ("customer_contract_archives", "total_amount", "ALTER TABLE customer_contract_archives ADD COLUMN total_amount DECIMAL(14,2) NULL COMMENT '合同总金额' AFTER assigned_at"),
        ("customer_contract_archives", "previous_total_amount", "ALTER TABLE customer_contract_archives ADD COLUMN previous_total_amount DECIMAL(14,2) NULL COMMENT '修改前合同总金额' AFTER total_amount"),
        ("price_split_france", "created_by_user_id", "ALTER TABLE price_split_france ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
        ("price_split_france", "updated_by_user_id", "ALTER TABLE price_split_france ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
        ("price_split_dalian", "created_by_user_id", "ALTER TABLE price_split_dalian ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by"),
        ("price_split_dalian", "updated_by_user_id", "ALTER TABLE price_split_dalian ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by"),
        ("pallet_archives", "box_weight_kg", PALLET_ARCHIVE_BOX_WEIGHT_SQL),
        ("price_split_france", "is_valid", "ALTER TABLE price_split_france ADD COLUMN is_valid TINYINT NOT NULL DEFAULT 1 COMMENT '1有效 0无效历史' AFTER updated_at"),
        ("price_split_france", "valid_slot", "ALTER TABLE price_split_france ADD COLUMN valid_slot TINYINT NULL COMMENT '有效行=1，无效行=NULL' AFTER is_valid"),
        ("price_split_france", "price_change_remark", "ALTER TABLE price_split_france ADD COLUMN price_change_remark VARCHAR(500) NULL COMMENT '调价/建档说明' AFTER price_version"),
        ("price_split_france", "replaces_id", "ALTER TABLE price_split_france ADD COLUMN replaces_id BIGINT NULL COMMENT '本行替换的旧行ID' AFTER price_change_remark"),
        ("price_split_france", "superseded_by_id", "ALTER TABLE price_split_france ADD COLUMN superseded_by_id BIGINT NULL COMMENT '被哪条新行替代' AFTER replaces_id"),
        ("price_split_dalian", "is_valid", "ALTER TABLE price_split_dalian ADD COLUMN is_valid TINYINT NOT NULL DEFAULT 1 COMMENT '1有效 0无效历史' AFTER updated_at"),
        ("price_split_dalian", "valid_slot", "ALTER TABLE price_split_dalian ADD COLUMN valid_slot TINYINT NULL COMMENT '有效行=1，无效行=NULL' AFTER is_valid"),
        ("price_split_dalian", "price_change_remark", "ALTER TABLE price_split_dalian ADD COLUMN price_change_remark VARCHAR(500) NULL COMMENT '调价/建档说明' AFTER price_version"),
        ("price_split_dalian", "replaces_id", "ALTER TABLE price_split_dalian ADD COLUMN replaces_id BIGINT NULL COMMENT '本行替换的旧行ID' AFTER price_change_remark"),
        ("price_split_dalian", "superseded_by_id", "ALTER TABLE price_split_dalian ADD COLUMN superseded_by_id BIGINT NULL COMMENT '被哪条新行替代' AFTER replaces_id"),
        ("price_split_france", "vat_rate", "ALTER TABLE price_split_france ADD COLUMN vat_rate DECIMAL(8,4) NULL COMMENT '增值税%，如13表示13%' AFTER excel_net_price"),
        ("price_split_france", "tax_inclusive_price", "ALTER TABLE price_split_france ADD COLUMN tax_inclusive_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '含税单价' AFTER vat_rate"),
        ("price_split_dalian", "vat_rate", "ALTER TABLE price_split_dalian ADD COLUMN vat_rate DECIMAL(8,4) NULL COMMENT '增值税%，如13表示13%' AFTER excel_net_price"),
        ("price_split_dalian", "tax_inclusive_price", "ALTER TABLE price_split_dalian ADD COLUMN tax_inclusive_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '含税单价' AFTER vat_rate"),
        ("france_customer_packing_lists", "amount_checked", "ALTER TABLE france_customer_packing_lists ADD COLUMN amount_checked TINYINT NOT NULL DEFAULT 0 COMMENT '核对总单价是否已确认' AFTER source_file_name"),
        ("france_customer_packing_lists", "amount_checked_at", "ALTER TABLE france_customer_packing_lists ADD COLUMN amount_checked_at DATETIME NULL COMMENT '核对总单价确认时间' AFTER amount_checked"),
        ("france_customer_packing_lists", "packing_tax_sum", "ALTER TABLE france_customer_packing_lists ADD COLUMN packing_tax_sum DECIMAL(14,2) NULL COMMENT '核对当时的箱单含税合计' AFTER amount_checked_at"),
        ("france_customer_packing_lists", "contract_total_amount", "ALTER TABLE france_customer_packing_lists ADD COLUMN contract_total_amount DECIMAL(14,2) NULL COMMENT '核对当时的合同总金额' AFTER packing_tax_sum"),
        ("france_customer_packing_lists", "factory_order_no", "ALTER TABLE france_customer_packing_lists ADD COLUMN factory_order_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '本厂单号' AFTER contract_total_amount"),
        ("france_customer_packing_lists", "customer", "ALTER TABLE france_customer_packing_lists ADD COLUMN customer VARCHAR(128) NOT NULL DEFAULT '' COMMENT '客户' AFTER factory_order_no"),
        ("france_customer_packing_list_items", "tax_unit_price", "ALTER TABLE france_customer_packing_list_items ADD COLUMN tax_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '含税单价（保留2位）' AFTER agreement_price"),
        ("france_customer_packing_list_items", "tax_total_amount", "ALTER TABLE france_customer_packing_list_items ADD COLUMN tax_total_amount DECIMAL(14,2) NOT NULL DEFAULT 0.00 COMMENT '含税总价（含税单价×数量）' AFTER tax_unit_price"),
        ("typing_requirements", "part_name", "ALTER TABLE typing_requirements ADD COLUMN part_name VARCHAR(128) NULL COMMENT '料号：法国默认-，大连取订单明细名称/料号' AFTER material_no"),
        ("typing_requirements", "unit_weight", "ALTER TABLE typing_requirements ADD COLUMN unit_weight DECIMAL(12,2) NULL COMMENT '单重，取自订单明细' AFTER quantity"),
        ("typing_requirements", "spec", "ALTER TABLE typing_requirements ADD COLUMN spec VARCHAR(255) NULL COMMENT '规格（展示：spec_display 或 spec）' AFTER typing_content"),
        ("typing_requirements", "model_standard", "ALTER TABLE typing_requirements ADD COLUMN model_standard VARCHAR(255) NULL COMMENT '型号（标准）' AFTER spec"),
        ("typing_requirements", "qr_spec", "ALTER TABLE typing_requirements ADD COLUMN qr_spec VARCHAR(128) NULL COMMENT '二维码规格信息' AFTER model_standard"),
        ("workbench_typing_notices", "changed_rows", "ALTER TABLE workbench_typing_notices ADD COLUMN changed_rows TEXT NULL COMMENT '未对齐明细 JSON' AFTER owner_name"),
        ("workbench_typing_notices", "trigger_user_id", "ALTER TABLE workbench_typing_notices ADD COLUMN trigger_user_id INT NULL COMMENT '触发建档的操作人' AFTER owner_name"),
        ("workbench_typing_notices", "trigger_user_name", "ALTER TABLE workbench_typing_notices ADD COLUMN trigger_user_name VARCHAR(64) NULL AFTER trigger_user_id"),
        ("dalian_econtract_order_contracts", "defer_contract", "ALTER TABLE dalian_econtract_order_contracts ADD COLUMN defer_contract TINYINT NOT NULL DEFAULT 0 COMMENT '1=合同后补未关联归档合同' AFTER contract_total_amount"),
    ]

    with get_db() as conn:
        cursor = conn.cursor()
        for stmt in create_statements:
            cursor.execute(stmt)
        applied_alters = 0
        for table_name, column_name, stmt in alter_statements:
            if not has_column(cursor, table_name, column_name):
                cursor.execute(stmt)
                applied_alters += 1
        if has_table(cursor, "dalian_econtract_lists"):
            cursor.execute(
                "ALTER TABLE dalian_econtract_lists "
                "MODIFY contract_archive_id INT NOT NULL DEFAULT 0 COMMENT '关联客户合同 id，0表示整单后补'"
            )
            applied_alters += 1
        if has_table(cursor, "dalian_econtract_order_contracts"):
            cursor.execute(
                "ALTER TABLE dalian_econtract_order_contracts "
                "MODIFY contract_archive_id INT NOT NULL DEFAULT 0 COMMENT '关联客户合同 id，0表示后补未关联'"
            )
            applied_alters += 1
        if has_table(cursor, "heat_treatment_test_records"):
            if has_index(cursor, "heat_treatment_test_records", "uk_order_heat_batch"):
                cursor.execute("ALTER TABLE heat_treatment_test_records DROP INDEX uk_order_heat_batch")
                applied_alters += 1
            if has_index(cursor, "heat_treatment_test_records", "uk_heat_batch"):
                cursor.execute("ALTER TABLE heat_treatment_test_records DROP INDEX uk_heat_batch")
                applied_alters += 1
            cursor.execute(
                """
                DELETE t1 FROM heat_treatment_test_records t1
                INNER JOIN heat_treatment_test_records t2
                  ON t1.heat_no = t2.heat_no
                 AND t1.batch_no = t2.batch_no
                 AND t1.material_no = t2.material_no
                 AND t1.id < t2.id
                """
            )
            if not has_index(cursor, "heat_treatment_test_records", "uk_heat_batch_material"):
                cursor.execute("ALTER TABLE heat_treatment_test_records ADD UNIQUE KEY uk_heat_batch_material (heat_no, batch_no, material_no)")
                applied_alters += 1
            if not has_index(cursor, "heat_treatment_test_records", "idx_material_no"):
                cursor.execute("ALTER TABLE heat_treatment_test_records ADD INDEX idx_material_no (material_no)")
                applied_alters += 1
            if has_column(cursor, "heat_treatment_test_records", "order_no"):
                cursor.execute("ALTER TABLE heat_treatment_test_records DROP COLUMN order_no")
                applied_alters += 1
        if has_table(cursor, "heat_treatment_trial_records"):
            if has_column(cursor, "heat_treatment_trial_records", "material_no"):
                cursor.execute("UPDATE heat_treatment_trial_records SET material_no='' WHERE material_no IS NULL")
            if has_column(cursor, "heat_treatment_trial_records", "spec_model"):
                cursor.execute("UPDATE heat_treatment_trial_records SET spec_model='' WHERE spec_model IS NULL")
            if has_column(cursor, "heat_treatment_trial_records", "material_no"):
                cursor.execute(
                    "ALTER TABLE heat_treatment_trial_records "
                    "MODIFY COLUMN material_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物料号'"
                )
                applied_alters += 1
            if has_column(cursor, "heat_treatment_trial_records", "spec_model"):
                cursor.execute(
                    "ALTER TABLE heat_treatment_trial_records "
                    "MODIFY COLUMN spec_model VARCHAR(255) NOT NULL DEFAULT '' COMMENT '规格型号'"
                )
                applied_alters += 1
            cursor.execute(
                """
                DELETE t1 FROM heat_treatment_trial_records t1
                INNER JOIN heat_treatment_trial_records t2
                  ON t1.heat_no = t2.heat_no
                 AND t1.batch_no = t2.batch_no
                 AND t1.material_no = t2.material_no
                 AND t1.spec_model = t2.spec_model
                 AND t1.id < t2.id
                """
            )
            if not has_index(cursor, "heat_treatment_trial_records", "uk_trial_heat_batch_material_spec"):
                cursor.execute(
                    "ALTER TABLE heat_treatment_trial_records "
                    "ADD UNIQUE KEY uk_trial_heat_batch_material_spec (heat_no, batch_no, material_no, spec_model)"
                )
                applied_alters += 1
        if has_table(cursor, "qc_certificate_snapshots") and has_index(
            cursor, "qc_certificate_snapshots", "uk_qc_snapshot_business"
        ):
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.statistics
                WHERE table_schema = DATABASE()
                  AND table_name = 'qc_certificate_snapshots'
                  AND index_name = 'uk_qc_snapshot_business'
                ORDER BY seq_in_index
                """
            )
            snapshot_uk_cols = [str(r[0]).lower() for r in cursor.fetchall()]
            if snapshot_uk_cols and snapshot_uk_cols[0] != "order_detail_id":
                cursor.execute("ALTER TABLE qc_certificate_snapshots DROP INDEX uk_qc_snapshot_business")
                cursor.execute(
                    """
                    ALTER TABLE qc_certificate_snapshots
                    ADD UNIQUE KEY uk_qc_snapshot_business
                    (order_detail_id, order_no, material_no, item_no_key, heat_no, heat_treatment_batch_no)
                    """
                )
                applied_alters += 1
        if has_table(cursor, "dalian_certificate_allocations"):
            if not has_column(cursor, "dalian_certificate_allocations", "order_detail_id"):
                cursor.execute(
                    "ALTER TABLE dalian_certificate_allocations "
                    "ADD COLUMN order_detail_id INT NULL COMMENT '订单明细ID' AFTER id"
                )
                applied_alters += 1
            if has_index(cursor, "dalian_certificate_allocations", "uk_dalian_cert_business"):
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name = 'dalian_certificate_allocations'
                      AND index_name = 'uk_dalian_cert_business'
                    ORDER BY seq_in_index
                    """
                )
                dalian_uk_cols = [str(r[0]).lower() for r in cursor.fetchall()]
                if dalian_uk_cols != ["order_detail_id"]:
                    cursor.execute("ALTER TABLE dalian_certificate_allocations DROP INDEX uk_dalian_cert_business")
                    cursor.execute(
                        """
                        ALTER TABLE dalian_certificate_allocations
                        ADD UNIQUE KEY uk_dalian_cert_business (order_detail_id)
                        """
                    )
                    applied_alters += 1
            elif not has_index(cursor, "dalian_certificate_allocations", "uk_dalian_cert_business"):
                cursor.execute(
                    """
                    ALTER TABLE dalian_certificate_allocations
                    ADD UNIQUE KEY uk_dalian_cert_business (order_detail_id)
                    """
                )
                applied_alters += 1
            if not has_index(cursor, "dalian_certificate_allocations", "idx_dalian_cert_order_material"):
                cursor.execute(
                    """
                    ALTER TABLE dalian_certificate_allocations
                    ADD INDEX idx_dalian_cert_order_material (order_no, material_no, item_no_key)
                    """
                )
                applied_alters += 1
        from app.services.price_split_schema import UNIQUE_INDEX_SQL

        for table_name, region_key in (("price_split_france", "france"), ("price_split_dalian", "dalian")):
            if not has_table(cursor, table_name):
                continue
            if has_index(cursor, table_name, "uk_group_material"):
                cursor.execute(f"ALTER TABLE {table_name} DROP INDEX uk_group_material")
                applied_alters += 1
            if has_column(cursor, table_name, "valid_slot"):
                cursor.execute(f"UPDATE {table_name} SET valid_slot=1 WHERE is_valid=1 AND valid_slot IS NULL")
            if not has_index(cursor, table_name, "uk_valid_identity"):
                cursor.execute(UNIQUE_INDEX_SQL[region_key])
                applied_alters += 1
            if not has_index(cursor, table_name, "idx_is_valid"):
                cursor.execute(f"ALTER TABLE {table_name} ADD INDEX idx_is_valid (is_valid)")
                applied_alters += 1
        verification_targets = [
            ("table", "production_events"),
            ("table", "reconciliations"),
            ("table", "heat_treatment_test_records"),
            ("table", "heat_treatment_trial_records"),
            ("table", "heat_treatment_chemical_records"),
            ("table", "materials"),
            ("table", "dalian_shipping_details"),
            ("table", "dalian_certificate_allocations"),
            ("table", "dalian_certificate_daily_counters"),
            ("table", "qc_certificate_snapshots"),
            ("table", "price_split_france"),
            ("table", "price_split_dalian"),
            ("table", "price_split_operation_logs"),
            ("table", "order_detail_operation_logs"),
            ("table", "france_customer_packing_lists"),
            ("table", "france_customer_packing_list_items"),
            ("table", "france_customer_packing_operation_logs"),
            ("table", "france_customer_packing_order_contracts"),
            ("table", "dalian_econtract_lists"),
            ("table", "dalian_econtract_items"),
            ("table", "dalian_econtract_operation_logs"),
            ("table", "dalian_econtract_order_contracts"),
            ("table", "license_docs"),
            ("table", "license_certificate_items"),
            ("table", "paint_requirements"),
            ("table", "paint_requirement_logs"),
            ("table", "dn_inch_maps"),
            ("table", "dn_inch_map_logs"),
            ("table", "drawing_flange_type_maps"),
            ("table", "drawing_flange_type_map_logs"),
            ("table", "pallet_archives"),
            ("table", "pallet_archive_logs"),
            ("table", "france_auto_packing_qty_maps"),
            ("table", "france_auto_packing_qty_map_logs"),
            ("table", "dalian_auto_packing_qty_maps"),
            ("table", "dalian_auto_packing_qty_map_logs"),
            ("table", "workbench_qty_map_dn_notices"),
            ("table", "material_typing_maps"),
            ("table", "material_typing_map_logs"),
            ("table", "typing_requirements"),
            ("table", "typing_requirement_logs"),
            ("table", "qr_label_designs"),
            ("table", "qr_label_design_logs"),
            ("table", "workbench_typing_notices"),
            ("table", "dalian_shipping_reservations"),
            ("table", "dalian_shipping_date_assignments"),
            ("table", "dalian_shipping_progress_unreads"),
            ("table", "dalian_shipping_reservation_logs"),
            ("column", "workbench_typing_notices.changed_rows"),
            ("column", "workbench_typing_notices.trigger_user_id"),
            ("column", "workbench_typing_notices.trigger_user_name"),
            ("column", "typing_requirements.part_name"),
            ("column", "typing_requirements.unit_weight"),
            ("column", "typing_requirements.spec"),
            ("column", "typing_requirements.model_standard"),
            ("column", "typing_requirements.qr_spec"),
            ("column", "order_details.created_by"),
            ("column", "order_details.created_by_user_id"),
            ("column", "order_details.updated_by"),
            ("column", "order_details.updated_by_user_id"),
            ("column", "price_split_france.created_by_user_id"),
            ("column", "price_split_france.updated_by_user_id"),
            ("column", "price_split_dalian.created_by_user_id"),
            ("column", "price_split_dalian.updated_by_user_id"),
            ("column", "pallet_archives.box_weight_kg"),
            ("column", "price_split_france.is_valid"),
            ("column", "price_split_dalian.is_valid"),
            ("column", "price_split_france.price_change_remark"),
            ("column", "price_split_dalian.price_change_remark"),
            ("column", "price_split_france.vat_rate"),
            ("column", "price_split_france.tax_inclusive_price"),
            ("column", "document_jobs.file_name"),
            ("column", "order_details.packed_at"),
            ("column", "order_details.shipped_at"),
            ("column", "order_details.split_group_id"),
            ("column", "order_details.contract_qty"),
            ("column", "order_details.factory_order_no"),
            ("column", "order_details.item_no"),
            ("column", "packing_details.delivery_date"),
            ("column", "qc_records.certificate_path"),
            ("column", "heat_treatment_test_records.test_result"),
            ("column", "heat_treatment_test_records.remark"),
            ("column", "heat_treatment_test_records.material_no"),
            ("column", "materials.ut"),
            ("column", "materials.mt"),
            ("column", "materials.pt"),
            ("column", "materials.product_unit_price"),
            ("column", "materials.france_agreement_price"),
            ("column", "materials.dalian_agreement_price"),
            ("column", "materials.drawing_archive_id"),
            ("column", "france_customer_packing_list_items.box_no"),
            ("column", "france_customer_packing_list_items.actual_delivery_date"),
            ("column", "packing_details.created_by"),
            ("column", "packing_details.updated_by"),
            ("table", "france_auto_packing_logs"),
            ("column", "dalian_shipping_details.factory_order_no"),
            ("column", "dalian_shipping_details.created_by_user_id"),
            ("column", "dalian_shipping_details.updated_by"),
            ("column", "dalian_shipping_details.reservation_id"),
            ("column", "dalian_shipping_details.planned_arrival_date"),
            ("table", "dalian_packing_operation_logs"),
            ("column", "dalian_shipping_reservations.item_no"),
            ("column", "dalian_shipping_reservations.drawing_no"),
            ("column", "dalian_shipping_reservations.unit_weight"),
            ("column", "dalian_shipping_reservations.total_weight"),
            ("column", "dalian_shipping_date_assignments.packing_generated_by"),
            ("column", "dalian_shipping_date_assignments.packing_generated_by_user_id"),
            ("column", "dalian_shipping_date_assignments.packing_generated_at"),
            ("column", "dalian_shipping_date_assignments.packing_content_stale"),
            ("column", "france_customer_packing_lists.factory_order_no"),
            ("column", "france_customer_packing_lists.customer"),
            ("column", "qc_records.order_detail_id"),
            ("column", "qc_records.item_no"),
            ("column", "qc_records.item_no_key"),
            ("column", "qc_records.cert_date_yymmdd"),
            ("column", "qc_records.cert_daily_seq"),
            ("column", "qc_records.certificate_type"),
            ("column", "dalian_certificate_allocations.order_detail_id"),
            ("column", "dalian_econtract_order_contracts.defer_contract"),
            ("missing_column", "heat_treatment_test_records.order_no"),
        ]
        verified = []
        for kind, name in verification_targets:
            if kind == "table":
                verified.append(f"{name}={'OK' if has_table(cursor, name) else 'MISSING'}")
            elif kind == "column":
                table_name, column_name = name.split(".", 1)
                verified.append(f"{name}={'OK' if has_column(cursor, table_name, column_name) else 'MISSING'}")
            else:
                table_name, column_name = name.split(".", 1)
                verified.append(f"{name}={'REMOVED' if not has_column(cursor, table_name, column_name) else 'STILL_EXISTS'}")
        if has_table(cursor, "dalian_shipping_details") and not has_index(cursor, "dalian_shipping_details", "uk_dsd_reservation_packing"):
            cursor.execute(
                "ALTER TABLE dalian_shipping_details ADD UNIQUE KEY uk_dsd_reservation_packing (reservation_id, packing_no)"
            )
            applied_alters += 1
        if has_table(cursor, "dalian_shipping_details") and not has_index(cursor, "dalian_shipping_details", "idx_dsd_planned_arrival"):
            cursor.execute("ALTER TABLE dalian_shipping_details ADD INDEX idx_dsd_planned_arrival (planned_arrival_date)")
            applied_alters += 1
        if has_table(cursor, "order_details") and not has_index(cursor, "order_details", "idx_order_details_split_group"):
            cursor.execute("ALTER TABLE order_details ADD INDEX idx_order_details_split_group (split_group_id)")
            applied_alters += 1
        if has_table(cursor, "materials") and not has_index(cursor, "materials", "idx_material_drawing_archive"):
            cursor.execute("ALTER TABLE materials ADD INDEX idx_material_drawing_archive (drawing_archive_id)")
            applied_alters += 1
        cursor.close()

    from app.services.material_drawing_link import backfill_material_drawing_archive_ids

    backfill = backfill_material_drawing_archive_ids()
    print(f"Applied {len(create_statements)} create statements and {applied_alters} alter statements.")
    print(f"Material drawing backfill: linked={backfill.get('linked')} unmatched={backfill.get('unmatched')}")
    print("Verification: " + ", ".join(verified))


if __name__ == "__main__":
    main()
