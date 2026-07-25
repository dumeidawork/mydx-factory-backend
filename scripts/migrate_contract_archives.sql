-- 客户原始合同归档 + 基础用户种子（可对已有库单独执行）
SET NAMES utf8mb4;
USE mingyuan_erp;

CREATE TABLE IF NOT EXISTS customer_contract_archives (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_name VARCHAR(128) NOT NULL COMMENT '客户名',
    file_name VARCHAR(255) NOT NULL COMMENT '原始文件名',
    version VARCHAR(32) NOT NULL DEFAULT 'V0.0.0' COMMENT '版本号',
    upload_date DATE NOT NULL COMMENT '上传日期(可手选)',
    uploaded_by_user_id INT NULL,
    uploaded_by_name VARCHAR(64) NOT NULL DEFAULT '',
    updated_date DATE NULL COMMENT '更新日期(可手选，首次上传可空)',
    updated_by_user_id INT NULL,
    updated_by_name VARCHAR(64) NOT NULL DEFAULT '',
    summary TEXT NULL COMMENT '合同概述',
    owner_user_id INT NULL COMMENT '业务负责人',
    owner_name VARCHAR(64) NULL,
    storage_relative_path VARCHAR(1024) NOT NULL COMMENT '相对 contract_archives 根目录',
    file_size BIGINT NOT NULL DEFAULT 0,
    content_type VARCHAR(128) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_archive_identity (customer_name, file_name, version, upload_date),
    INDEX idx_archive_customer (customer_name),
    INDEX idx_archive_owner (owner_user_id),
    INDEX idx_archive_upload_date (upload_date),
    INDEX idx_archive_updated_date (updated_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户原始合同归档';

CREATE TABLE IF NOT EXISTS customer_contract_archive_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    archive_id INT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'create/update_meta/version_update/download/assign_owner/export/delete',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT NULL COMMENT '变更内容 JSON/文本',
    INDEX idx_archive_log_archive (archive_id),
    INDEX idx_archive_log_action (action),
    INDEX idx_archive_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户原始合同归档操作日志';

-- 默认管理员：admin / Admin@123456（首次部署后请立即修改密码）
-- password_hash 由 scripts/seed_admin.py 写入；此处仅保证角色存在
INSERT IGNORE INTO roles (id, code, name_zh, name_en, name_ja) VALUES
(1, 'super_admin', '董事长', 'Super Admin', '取締役');
