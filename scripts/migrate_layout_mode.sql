-- 用户布局偏好：horizontal / vertical / classic
SET NAMES utf8mb4;
USE mingyuan_erp;

SET @col_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'users'
      AND column_name = 'layout_mode'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE users ADD COLUMN layout_mode VARCHAR(16) NOT NULL DEFAULT ''horizontal'' COMMENT ''horizontal, vertical, classic'' AFTER language_preference',
    'SELECT 1'
);
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
