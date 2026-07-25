-- 订单明细：图纸缺失预警字段（可对已有库单独执行；应用启动时也会自动补列）
SET NAMES utf8mb4;
USE mingyuan_erp;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'order_details'
      AND column_name = 'doc_status'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE order_details ADD COLUMN doc_status VARCHAR(32) NOT NULL DEFAULT '''' COMMENT ''资料状态：空/待补资料'' AFTER order_status',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
