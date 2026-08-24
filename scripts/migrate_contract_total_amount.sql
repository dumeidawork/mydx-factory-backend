-- 合同归档总金额 + 金额修改前备份（可对已有库单独执行；应用启动时也会自动补列）
SET NAMES utf8mb4;
USE mingyuan_erp;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'customer_contract_archives'
      AND column_name = 'total_amount'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE customer_contract_archives ADD COLUMN total_amount DECIMAL(14,2) NULL COMMENT ''合同总金额'' AFTER assigned_at',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'customer_contract_archives'
      AND column_name = 'previous_total_amount'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE customer_contract_archives ADD COLUMN previous_total_amount DECIMAL(14,2) NULL COMMENT ''修改前合同总金额'' AFTER total_amount',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
