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
        "CREATE TABLE IF NOT EXISTS heat_treatment_trial_records (id BIGINT AUTO_INCREMENT PRIMARY KEY, test_date VARCHAR(8), heat_no VARCHAR(50) NOT NULL, batch_no VARCHAR(50) NOT NULL, spec_model VARCHAR(255), mech_yield_strength VARCHAR(50) NOT NULL, mech_tensile_strength VARCHAR(50) NOT NULL, mech_elongation VARCHAR(50) NOT NULL, mech_reduction_area VARCHAR(50) NOT NULL, mech_hardness_1 VARCHAR(50) NOT NULL, mech_hardness_2 VARCHAR(50) NOT NULL, mech_hardness_3 VARCHAR(50) NOT NULL, mech_impact_test VARCHAR(100) NOT NULL, seq_no VARCHAR(50), supplier_hardness VARCHAR(100), material_no VARCHAR(128), remark VARCHAR(255), material VARCHAR(64), updated_by VARCHAR(50) NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_trial_heat_batch (heat_no, batch_no))",
        "CREATE TABLE IF NOT EXISTS heat_treatment_chemical_records (id BIGINT AUTO_INCREMENT PRIMARY KEY, heat_no VARCHAR(50) NOT NULL, chem_raw_c DECIMAL(8,4), chem_raw_mn DECIMAL(8,4), chem_raw_p DECIMAL(8,4), chem_raw_s DECIMAL(8,4), chem_raw_si DECIMAL(8,4), chem_raw_cu DECIMAL(8,4), chem_raw_ni DECIMAL(8,4), chem_raw_cr DECIMAL(8,4), chem_raw_mo DECIMAL(8,4), chem_raw_v DECIMAL(8,4), chem_raw_ceq DECIMAL(8,4), chem_self_c DECIMAL(8,4), chem_self_mn DECIMAL(8,4), chem_self_p DECIMAL(8,4), chem_self_s DECIMAL(8,4), chem_self_si DECIMAL(8,4), chem_self_cu DECIMAL(8,4), chem_self_ni DECIMAL(8,4), chem_self_cr DECIMAL(8,4), chem_self_mo DECIMAL(8,4), chem_self_v DECIMAL(8,4), chem_self_ceq DECIMAL(8,4), updated_by VARCHAR(50) NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_chemical_heat_no (heat_no))",
        "CREATE TABLE IF NOT EXISTS materials (id BIGINT AUTO_INCREMENT PRIMARY KEY, material_no VARCHAR(100) NOT NULL, part_no VARCHAR(100) NULL, drawing_no VARCHAR(255) NULL, spec_model VARCHAR(255) NULL, material VARCHAR(100) NULL, unit_weight DECIMAL(10,3) NOT NULL DEFAULT 0.000, remark TEXT NULL, created_by VARCHAR(50) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_by VARCHAR(50) NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_material_no (material_no), INDEX idx_part_no (part_no), INDEX idx_drawing_no (drawing_no))",
        "CREATE TABLE IF NOT EXISTS dalian_shipping_details (id BIGINT AUTO_INCREMENT PRIMARY KEY, delivery_date VARCHAR(8) NOT NULL, packing_no VARCHAR(50) NULL, order_no VARCHAR(100) NOT NULL, item_no VARCHAR(50) NOT NULL, drawing_no VARCHAR(255) NULL, ap1_material_no VARCHAR(100) NULL, r3p_material_no VARCHAR(100) NULL, spec_model VARCHAR(255) NULL, material VARCHAR(100) NULL, quantity INT DEFAULT 0, unit_weight DECIMAL(10,3) DEFAULT 0.000, total_weight DECIMAL(10,3) DEFAULT 0.000, unit_price DECIMAL(10,2) NULL, remark TEXT NULL, buyer VARCHAR(100) NULL, heat_no VARCHAR(100) NULL, heat_treatment_batch_no VARCHAR(100) NULL, shipping_mark_remark VARCHAR(255) NULL, created_by VARCHAR(50) NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_delivery_date (delivery_date), INDEX idx_order_item (order_no, item_no)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS dalian_certificate_allocations (id INT PRIMARY KEY AUTO_INCREMENT, order_no VARCHAR(64) NOT NULL, material_no VARCHAR(128) NOT NULL, item_no_key INT NOT NULL DEFAULT 0, certificate_no VARCHAR(64) NOT NULL, cert_date_yymmdd VARCHAR(6) NOT NULL, cert_daily_seq INT NOT NULL, first_assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_dalian_cert_business (order_no, material_no, item_no_key), INDEX idx_dalian_cert_date (cert_date_yymmdd)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS dalian_certificate_daily_counters (cert_date_yymmdd VARCHAR(6) PRIMARY KEY, next_seq INT NOT NULL DEFAULT 1) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
        "CREATE TABLE IF NOT EXISTS qc_certificate_snapshots (id INT PRIMARY KEY AUTO_INCREMENT, order_detail_id INT NULL, order_no VARCHAR(64) NOT NULL, material_no VARCHAR(128) NOT NULL, item_no INT NULL, item_no_key INT NOT NULL DEFAULT 0, heat_no VARCHAR(64) NOT NULL DEFAULT '', heat_treatment_batch_no VARCHAR(64) NOT NULL DEFAULT '', certificate_type VARCHAR(16) NOT NULL, base_info JSON, delivery_content JSON, mechanical_tests JSON, chemical_analysis JSON, latest_qc_record_id INT NULL, certificate_path VARCHAR(512), generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, UNIQUE KEY uk_qc_snapshot_business (order_no, material_no, item_no_key, heat_no, heat_treatment_batch_no), INDEX idx_qc_snapshot_order (order_no), INDEX idx_qc_snapshot_material (material_no), INDEX idx_qc_snapshot_detail (order_detail_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4",
    ]
    alter_statements = [
        ("document_jobs", "file_name", "ALTER TABLE document_jobs ADD COLUMN file_name VARCHAR(255) NULL AFTER status"),
        ("order_details", "packed_at", "ALTER TABLE order_details ADD COLUMN packed_at DATETIME NULL AFTER finished_at"),
        ("order_details", "shipped_at", "ALTER TABLE order_details ADD COLUMN shipped_at DATETIME NULL AFTER packed_at"),
        ("order_details", "factory_order_no", "ALTER TABLE order_details ADD COLUMN factory_order_no VARCHAR(128) NULL AFTER customer"),
        ("order_details", "item_no", "ALTER TABLE order_details ADD COLUMN item_no INT NULL AFTER seq"),
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
        ("dalian_shipping_details", "factory_order_no", "ALTER TABLE dalian_shipping_details ADD COLUMN factory_order_no VARCHAR(128) NULL COMMENT '本厂单号' AFTER order_no"),
        ("qc_records", "order_detail_id", "ALTER TABLE qc_records ADD COLUMN order_detail_id INT NULL AFTER certificate_path"),
        ("qc_records", "item_no", "ALTER TABLE qc_records ADD COLUMN item_no INT NULL AFTER order_detail_id"),
        ("qc_records", "item_no_key", "ALTER TABLE qc_records ADD COLUMN item_no_key INT NOT NULL DEFAULT 0 AFTER item_no"),
        ("qc_records", "cert_date_yymmdd", "ALTER TABLE qc_records ADD COLUMN cert_date_yymmdd VARCHAR(6) NULL AFTER item_no_key"),
        ("qc_records", "cert_daily_seq", "ALTER TABLE qc_records ADD COLUMN cert_daily_seq INT NULL AFTER cert_date_yymmdd"),
        ("qc_records", "certificate_type", "ALTER TABLE qc_records ADD COLUMN certificate_type VARCHAR(16) NULL AFTER cert_daily_seq"),
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
            ("column", "document_jobs.file_name"),
            ("column", "order_details.packed_at"),
            ("column", "order_details.shipped_at"),
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
            ("column", "dalian_shipping_details.factory_order_no"),
            ("column", "qc_records.order_detail_id"),
            ("column", "qc_records.item_no"),
            ("column", "qc_records.item_no_key"),
            ("column", "qc_records.cert_date_yymmdd"),
            ("column", "qc_records.cert_daily_seq"),
            ("column", "qc_records.certificate_type"),
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
        cursor.close()

    print(f"Applied {len(create_statements)} create statements and {applied_alters} alter statements.")
    print("Verification: " + ", ".join(verified))


if __name__ == "__main__":
    main()
