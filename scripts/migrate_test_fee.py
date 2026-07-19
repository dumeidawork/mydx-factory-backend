import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import execute, fetch_one

def migrate():
    try:
        column = fetch_one(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'order_details'
              AND COLUMN_NAME = 'test_date'
            """
        )
        if column:
            print("order_details.test_date already exists")
        else:
            execute("ALTER TABLE `order_details` ADD COLUMN `test_date` VARCHAR(6) NULL COMMENT '试验日期(YYMMDD)' AFTER `heat_treatment_batch_no`")
            print("Added test_date to order_details")

        # Create test_fee_records
        execute("""
        CREATE TABLE IF NOT EXISTS `test_fee_records` (
          `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
          `test_date` VARCHAR(6) NOT NULL COMMENT '试验日期',
          `material_no` VARCHAR(50) NOT NULL COMMENT '物料号',
          `specification` VARCHAR(255) COMMENT '规格型号',
          `material` VARCHAR(100) COMMENT '材质',
          `quantity` INT COMMENT '数量',
          `remark` VARCHAR(255) COMMENT '备注(取自remark1)',
          `order_no` VARCHAR(50) NOT NULL COMMENT '订单号',
          `heat_no` VARCHAR(50) NOT NULL COMMENT '炉号',
          `heat_treatment_batch_no` VARCHAR(50) NOT NULL COMMENT '热处理批号',
          
          `yield_strength` VARCHAR(50) COMMENT '屈服',
          `tensile_strength` VARCHAR(50) COMMENT '抗拉',
          `elongation` VARCHAR(50) COMMENT '延伸',
          `reduction_of_area` VARCHAR(50) COMMENT '断面收缩',
          `hardness` VARCHAR(100) COMMENT '硬度',
          `impact_work` VARCHAR(100) COMMENT '冲击',
          
          `product_unit_price` DECIMAL(10,2) NULL COMMENT '产品单价',
          `test_qty_impact` INT NULL COMMENT '实验数量 冲击',
          `test_qty_tensile` INT NULL COMMENT '实验数量 拉伸',
          `sample_fee_impact` DECIMAL(10,2) NULL COMMENT '取样费用 冲击',
          `sample_fee_tensile` DECIMAL(10,2) NULL COMMENT '取样费用 拉伸',
          `test_fee_impact` DECIMAL(10,2) NULL COMMENT '试验费用 冲击',
          `test_fee_tensile` DECIMAL(10,2) NULL COMMENT '试验费用 拉伸',
          
          `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
          
          UNIQUE KEY `uk_order_mat_heat_batch` (`order_no`, `material_no`, `heat_no`, `heat_treatment_batch_no`),
          INDEX `idx_test_date` (`test_date`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验费用整理表'
        """)
        print("Created test_fee_records table")

        drawing_col = fetch_one(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'test_fee_records'
              AND COLUMN_NAME = 'drawing_no'
            """
        )
        if not drawing_col:
            execute("ALTER TABLE `test_fee_records` ADD COLUMN `drawing_no` VARCHAR(255) NULL COMMENT '图纸号' AFTER `material_no`")
            print("Added drawing_no to test_fee_records")

        item_col = fetch_one(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'test_fee_records'
              AND COLUMN_NAME = 'item_no'
            """
        )
        if not item_col:
            execute("ALTER TABLE `test_fee_records` ADD COLUMN `item_no` INT NULL COMMENT '条目' AFTER `order_no`")
            print("Added item_no to test_fee_records")
    except Exception as e:
        print(f"Migration failed: {e}")

if __name__ == '__main__':
    migrate()
