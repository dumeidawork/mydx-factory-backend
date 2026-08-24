"""资材获取：可访问根目录表。"""

CREATE_RESOURCE_FETCH_ROOTS_SQL = """
CREATE TABLE IF NOT EXISTS resource_fetch_roots (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(128) NOT NULL COMMENT '界面显示别名',
    absolute_path VARCHAR(1024) NOT NULL COMMENT '服务器绝对路径',
    enabled TINYINT NOT NULL DEFAULT 1 COMMENT '1启用 0停用',
    sort_order INT NOT NULL DEFAULT 0,
    remark VARCHAR(255) NOT NULL DEFAULT '',
    created_by VARCHAR(64) NOT NULL DEFAULT '',
    created_by_user_id INT NULL,
    updated_by VARCHAR(64) NOT NULL DEFAULT '',
    updated_by_user_id INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_rf_roots_enabled (enabled, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资材获取可访问根目录'
"""

CREATE_RESOURCE_FETCH_ROOT_ROLES_SQL = """
CREATE TABLE IF NOT EXISTS resource_fetch_root_roles (
    root_id BIGINT NOT NULL,
    role_id INT NOT NULL,
    PRIMARY KEY (root_id, role_id),
    INDEX idx_rf_root_roles_role (role_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资材获取根目录允许的角色'
"""

CREATE_RESOURCE_FETCH_DOWNLOAD_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS resource_fetch_download_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    root_id BIGINT NOT NULL COMMENT '根目录ID',
    root_name VARCHAR(128) NOT NULL DEFAULT '' COMMENT '下载时的根目录显示名',
    rel_path VARCHAR(1024) NOT NULL DEFAULT '' COMMENT '相对根目录的路径',
    file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '下载文件名',
    item_kind VARCHAR(16) NOT NULL DEFAULT 'file' COMMENT 'file / dir / archive',
    downloaded_by VARCHAR(64) NOT NULL DEFAULT '',
    downloaded_by_user_id INT NULL,
    downloaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_rf_dl_time (downloaded_at),
    INDEX idx_rf_dl_root (root_id, downloaded_at),
    INDEX idx_rf_dl_user (downloaded_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资材获取下载动态'
"""
