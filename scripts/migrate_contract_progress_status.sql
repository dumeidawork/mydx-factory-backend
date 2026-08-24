-- 合同归档进度状态：上传 / 分配 / 确认（可对已有库单独执行；应用启动时也会自动补列）
SET NAMES utf8mb4;
USE mingyuan_erp;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'customer_contract_archives'
      AND column_name = 'progress_status'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE customer_contract_archives ADD COLUMN progress_status VARCHAR(16) NOT NULL DEFAULT ''上传'' COMMENT ''合同进度：上传/分配/确认'' AFTER owner_name',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'customer_contract_archives'
      AND column_name = 'assigned_at'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE customer_contract_archives ADD COLUMN assigned_at DATETIME NULL COMMENT ''进入本次分配的时间'' AFTER progress_status',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @idx_exists := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'customer_contract_archives'
      AND index_name = 'idx_archive_owner_progress'
);

SET @sql := IF(
    @idx_exists = 0,
    'ALTER TABLE customer_contract_archives ADD INDEX idx_archive_owner_progress (owner_user_id, progress_status)',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 历史数据：已指定负责人的合同进入【分配】，业务员须补一次接手
UPDATE customer_contract_archives
SET progress_status = '分配'
WHERE owner_user_id IS NOT NULL
  AND owner_user_id <> 0
  AND IFNULL(progress_status, '上传') = '上传';

UPDATE customer_contract_archives a
LEFT JOIN (
    SELECT archive_id, MAX(operated_at) AS last_assign
    FROM customer_contract_archive_logs
    WHERE action = 'assign_owner'
    GROUP BY archive_id
) l ON l.archive_id = a.id
SET a.assigned_at = COALESCE(l.last_assign, a.created_at)
WHERE a.progress_status = '分配'
  AND a.assigned_at IS NULL
  AND a.owner_user_id IS NOT NULL
  AND a.owner_user_id <> 0;
