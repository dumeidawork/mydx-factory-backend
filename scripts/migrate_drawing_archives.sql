-- 图纸建档：主表 + 操作日志（可对已有库单独执行）
SET NAMES utf8mb4;
USE mingyuan_erp;

CREATE TABLE IF NOT EXISTS drawing_archives (
    id INT PRIMARY KEY AUTO_INCREMENT,
    customer_code VARCHAR(64) NOT NULL COMMENT '客户代码',
    drawing_no VARCHAR(255) NOT NULL COMMENT '标准号/图纸号-版本号（与订单 drawing_no 精确匹配）',
    material VARCHAR(128) NOT NULL COMMENT '材质',
    spec_model VARCHAR(255) NOT NULL COMMENT '规格型号/物料号',
    drawing_type VARCHAR(128) NOT NULL COMMENT '类型',
    drawing_name VARCHAR(512) NOT NULL COMMENT '图纸名（不含.pdf）',
    storage_relative_path VARCHAR(1024) NOT NULL COMMENT '相对 drawing_archives 根目录',
    file_size BIGINT NOT NULL DEFAULT 0,
    content_type VARCHAR(128) NULL,
    uploaded_at DATETIME NOT NULL COMMENT '上传时间（服务器本地时间，自动写入）',
    uploaded_by_user_id INT NULL,
    uploaded_by_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '上传者姓名',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_drawing_name (drawing_name),
    INDEX idx_drawing_no (drawing_no),
    INDEX idx_drawing_customer (customer_code),
    INDEX idx_drawing_uploaded_at (uploaded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='图纸建档';

CREATE TABLE IF NOT EXISTS drawing_archive_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    archive_id INT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'upload/download/batch_download/delete',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT NULL COMMENT '变更内容 JSON/文本',
    INDEX idx_drawing_log_archive (archive_id),
    INDEX idx_drawing_log_action (action),
    INDEX idx_drawing_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='图纸建档操作日志';
