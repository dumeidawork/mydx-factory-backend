-- 大连电子合同：主表 + 明细 + 操作日志
-- 原件只读取不归档；单头仅记来源文件名

CREATE TABLE IF NOT EXISTS dalian_econtract_lists (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '关联合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '关联合同版本号快照',
    source_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '上传时的文件名（不存原件）',
    factory_order_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '本厂单号',
    customer VARCHAR(128) NOT NULL DEFAULT '' COMMENT '客户',
    price_checked TINYINT NOT NULL DEFAULT 0 COMMENT '核对单价是否已确认',
    price_checked_at DATETIME NULL COMMENT '核对单价确认时间',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_by_user_id INT NULL COMMENT '上传者用户ID',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_by_user_id INT NULL COMMENT '更新者用户ID',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_decl_contract (contract_archive_id),
    INDEX idx_decl_created_by (created_by_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同主表';

CREATE TABLE IF NOT EXISTS dalian_econtract_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '主表 id',
    seq INT NOT NULL DEFAULT 0 COMMENT '序号',
    part_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '料号',
    material_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物料号',
    dn VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'DN大小',
    order_no VARCHAR(64) NOT NULL DEFAULT '' COMMENT '订单号',
    excel_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '电子合同单价',
    split_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '价格拆分表净价',
    material_unit_price DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '物料大连协议价',
    price_mismatch TINYINT NOT NULL DEFAULT 0 COMMENT 'Excel单价与拆分/物料价不一致',
    quantity INT NOT NULL DEFAULT 0 COMMENT '数量',
    drawing_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号',
    spec_model VARCHAR(256) NOT NULL DEFAULT '' COMMENT '规格型号',
    spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格',
    model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号',
    material VARCHAR(64) NOT NULL DEFAULT '' COMMENT '材质',
    unit_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '单重',
    total_weight DECIMAL(12,2) NOT NULL DEFAULT 0.00 COMMENT '总重',
    remark1 VARCHAR(255) NOT NULL DEFAULT '' COMMENT '备注',
    drawing_candidates JSON NULL COMMENT '可选图纸号列表',
    multi_drawing TINYINT NOT NULL DEFAULT 0 COMMENT '是否存在多个图纸版本',
    drawing_confirmed TINYINT NOT NULL DEFAULT 0 COMMENT '多版本是否已确认',
    match_warning VARCHAR(255) NOT NULL DEFAULT '' COMMENT '回填警告',
    created_by VARCHAR(64) NULL COMMENT '上传者',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
    updated_by VARCHAR(64) NULL COMMENT '更新者',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_deci_list (list_id),
    INDEX idx_deci_order (order_no),
    INDEX idx_deci_material (material_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同明细';

CREATE TABLE IF NOT EXISTS dalian_econtract_operation_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NULL,
    item_id BIGINT NULL,
    action VARCHAR(32) NOT NULL COMMENT 'parse/create/update/delete/overwrite/generate_order',
    operator_user_id INT NULL,
    operator_name VARCHAR(64) NOT NULL DEFAULT '',
    operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    change_summary TEXT NULL COMMENT '变更内容 JSON',
    INDEX idx_decl_log_list (list_id),
    INDEX idx_decl_log_item (item_id),
    INDEX idx_decl_log_action (action),
    INDEX idx_decl_log_time (operated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同操作日志';

CREATE TABLE IF NOT EXISTS dalian_econtract_order_contracts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    list_id BIGINT NOT NULL COMMENT '电子合同主表 id',
    order_no VARCHAR(64) NOT NULL COMMENT '订单号',
    contract_archive_id INT NOT NULL COMMENT '关联客户合同 id',
    contract_file_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '合同文件名快照',
    contract_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '合同版本号快照',
    contract_total_amount DECIMAL(14,2) NULL COMMENT '关联时的合同总金额',
    UNIQUE KEY uk_decl_oc_list_order (list_id, order_no),
    INDEX idx_decl_oc_list (list_id),
    INDEX idx_decl_oc_contract (contract_archive_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连电子合同：订单号与合同一对一';
