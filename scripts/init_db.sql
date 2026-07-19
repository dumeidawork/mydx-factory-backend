-- 铭远ERP 数据库初始化脚本 (MySQL 8.0+)
-- 核心表概览（与文档 5 节对应）

SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS mingyuan_erp DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE mingyuan_erp;

-- 角色表 (RBAC)
CREATE TABLE IF NOT EXISTS roles (
    id INT PRIMARY KEY AUTO_INCREMENT,
    code VARCHAR(32) NOT NULL UNIQUE COMMENT 'Super Admin, General Manager, Finance, Sales, Purchasing, Production Manager, QC, Warehouse',
    name_zh VARCHAR(64) NOT NULL,
    name_en VARCHAR(64),
    name_ja VARCHAR(64),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 用户表
CREATE TABLE IF NOT EXISTS users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(64) NOT NULL,
    role_id INT NOT NULL,
    department VARCHAR(64),
    phone VARCHAR(32),
    language_preference VARCHAR(10) DEFAULT 'zh-CN' COMMENT 'zh-CN, en-US, ja-JP',
    username VARCHAR(64) UNIQUE,
    password_hash VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (role_id) REFERENCES roles(id)
);

-- 合同表
CREATE TABLE IF NOT EXISTS contracts (
    id INT PRIMARY KEY AUTO_INCREMENT,
    client_name VARCHAR(128) NOT NULL,
    drawing_type VARCHAR(64),
    total_amount DECIMAL(14,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'draft',
    client_language VARCHAR(10) DEFAULT 'zh-CN' COMMENT '用于生成对账单语言',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 生产工单表
CREATE TABLE IF NOT EXISTS production_orders (
    id INT PRIMARY KEY AUTO_INCREMENT,
    contract_id INT,
    material_type VARCHAR(64) COMMENT '本厂钢材/外来毛坯/外来成品',
    current_node VARCHAR(64),
    status VARCHAR(32) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (contract_id) REFERENCES contracts(id)
);

CREATE TABLE IF NOT EXISTS production_events (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT NOT NULL,
    node VARCHAR(64) NOT NULL,
    operator VARCHAR(64),
    remark VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_prod_event_order (order_id)
);

-- 财务收支表
CREATE TABLE IF NOT EXISTS financial_records (
    id INT PRIMARY KEY AUTO_INCREMENT,
    type VARCHAR(16) NOT NULL COMMENT 'income, expense',
    category VARCHAR(64),
    amount DECIMAL(14,2) NOT NULL,
    status VARCHAR(32) DEFAULT 'pending',
    voucher_url VARCHAR(512),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    contract_no VARCHAR(64) NOT NULL,
    client_name VARCHAR(128) NOT NULL,
    amount DECIMAL(14,2) NOT NULL,
    currency VARCHAR(16) DEFAULT 'CNY',
    invoice_no VARCHAR(64),
    status VARCHAR(32) DEFAULT 'generated',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 质检记录表
CREATE TABLE IF NOT EXISTS quality_inspections (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT,
    inspector_id INT,
    result VARCHAR(16) COMMENT 'pass, fail, rework',
    remark TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES production_orders(id),
    FOREIGN KEY (inspector_id) REFERENCES users(id)
);

-- 售后与追责表
CREATE TABLE IF NOT EXISTS after_sales (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_id INT,
    issue_description TEXT,
    responsible_user_id INT,
    penalty_amount DECIMAL(14,2) DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES production_orders(id),
    FOREIGN KEY (responsible_user_id) REFERENCES users(id)
);

-- 单据模板表（质保书/装箱单/唛头）
CREATE TABLE IF NOT EXISTS document_templates (
    id INT PRIMARY KEY AUTO_INCREMENT,
    doc_type VARCHAR(32) NOT NULL COMMENT 'quality_certificate, packing_list, shipping_mark',
    language VARCHAR(10) DEFAULT 'zh-CN',
    version VARCHAR(16) DEFAULT 'v1.0',
    template_name VARCHAR(128) NOT NULL,
    content TEXT,
    is_active TINYINT(1) DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_doc_type_lang_version (doc_type, language, version)
);

-- 单据导出任务表
CREATE TABLE IF NOT EXISTS document_jobs (
    id INT PRIMARY KEY AUTO_INCREMENT,
    doc_type VARCHAR(32) NOT NULL,
    order_no VARCHAR(64) NOT NULL,
    language VARCHAR(10) DEFAULT 'zh-CN',
    status VARCHAR(32) DEFAULT 'pending' COMMENT 'pending, completed, failed',
    file_name VARCHAR(255),
    file_path VARCHAR(512),
    created_by INT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 唛头内容记录表
CREATE TABLE IF NOT EXISTS shipping_marks (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    box_no VARCHAR(32) NOT NULL,
    client_name VARCHAR(128),
    destination_port VARCHAR(128),
    mark_line_1 VARCHAR(128),
    mark_line_2 VARCHAR(128),
    mark_line_3 VARCHAR(128),
    remark VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 订单明细表（接收合同/手动录入）
CREATE TABLE IF NOT EXISTS order_details (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    customer VARCHAR(128) NOT NULL,
    factory_order_no VARCHAR(128),
    seq INT NOT NULL,
    item_no INT,
    name VARCHAR(128),
    drawing_no VARCHAR(128),
    material_no VARCHAR(128) NOT NULL,
    spec_model VARCHAR(256),
    spec VARCHAR(64),
    standard VARCHAR(64),
    material VARCHAR(64),
    quantity INT DEFAULT 0,
    unit_weight DECIMAL(12,2) DEFAULT 0,
    total_weight DECIMAL(12,2) DEFAULT 0,
    remark1 VARCHAR(255),
    remark2 VARCHAR(255),
    heat_no VARCHAR(64),
    heat_treatment_batch_no VARCHAR(64),
    order_status VARCHAR(32) DEFAULT '开始',
    upload_type VARCHAR(32) DEFAULT '手动录入',
    material_mode VARCHAR(32),
    started_at DATETIME,
    finished_at DATETIME,
    packed_at DATETIME,
    shipped_at DATETIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_order_no (order_no),
    INDEX idx_material_no (material_no),
    INDEX idx_status (order_status)
);

-- 装箱明细表
CREATE TABLE IF NOT EXISTS packing_details (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    material_no VARCHAR(128) NOT NULL,
    spec VARCHAR(64),
    standard VARCHAR(64),
    material VARCHAR(64),
    quantity INT DEFAULT 0,
    unit_weight DECIMAL(12,2) DEFAULT 0,
    total_weight DECIMAL(12,2) DEFAULT 0,
    gross_weight DECIMAL(12,2) DEFAULT 0,
    remark1 VARCHAR(255),
    box_no INT NOT NULL,
    entry_no INT DEFAULT 1,
    box_length INT,
    box_width INT,
    box_height INT,
    packing_remark VARCHAR(255),
    delivery_date DATE,
    packed_at DATETIME,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_pack_order (order_no),
    INDEX idx_pack_box (box_no)
);

-- 质检记录表（结构化JSON）
CREATE TABLE IF NOT EXISTS qc_records (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    material_no VARCHAR(128) NOT NULL,
    base_info JSON,
    delivery_content JSON,
    mechanical_tests JSON,
    chemical_analysis JSON,
    certificate_path VARCHAR(512),
    order_detail_id INT NULL COMMENT '关联订单明细ID',
    item_no INT NULL COMMENT '条目号',
    item_no_key INT NOT NULL DEFAULT 0 COMMENT '条目归一化键，空条目为0',
    cert_date_yymmdd VARCHAR(6) NULL COMMENT '证书编号中的YYMMDD',
    cert_daily_seq INT NULL COMMENT '证书编号当日流水后缀',
    certificate_type VARCHAR(16) NULL COMMENT 'dalian/france',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_qc_order_material (order_no, material_no)
);

-- 质保书快照表（每条业务数据当前已签发的质保书完整快照）
CREATE TABLE IF NOT EXISTS qc_certificate_snapshots (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_detail_id INT NULL COMMENT '最近生成时关联的订单明细ID',
    order_no VARCHAR(64) NOT NULL,
    material_no VARCHAR(128) NOT NULL,
    item_no INT NULL COMMENT '条目号',
    item_no_key INT NOT NULL DEFAULT 0 COMMENT '条目归一化键，空条目为0',
    heat_no VARCHAR(64) NOT NULL DEFAULT '',
    heat_treatment_batch_no VARCHAR(64) NOT NULL DEFAULT '',
    certificate_type VARCHAR(16) NOT NULL COMMENT 'france/dalian',
    base_info JSON,
    delivery_content JSON,
    mechanical_tests JSON,
    chemical_analysis JSON,
    latest_qc_record_id INT NULL COMMENT '最近一次生成对应的qc_records.id',
    certificate_path VARCHAR(512),
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '首次成功生成时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_qc_snapshot_business (order_no, material_no, item_no_key, heat_no, heat_treatment_batch_no),
    INDEX idx_qc_snapshot_order (order_no),
    INDEX idx_qc_snapshot_material (material_no),
    INDEX idx_qc_snapshot_detail (order_detail_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='质保书快照（按业务五元组唯一）';

-- 大连质保书证书编号分配表（业务键幂等）
CREATE TABLE IF NOT EXISTS dalian_certificate_allocations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    order_no VARCHAR(64) NOT NULL,
    material_no VARCHAR(128) NOT NULL,
    item_no_key INT NOT NULL DEFAULT 0 COMMENT 'COALESCE(item_no,0)',
    certificate_no VARCHAR(64) NOT NULL,
    cert_date_yymmdd VARCHAR(6) NOT NULL,
    cert_daily_seq INT NOT NULL,
    first_assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_dalian_cert_business (order_no, material_no, item_no_key),
    INDEX idx_dalian_cert_date (cert_date_yymmdd)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连质保书证书编号分配';

-- 大连质保书按日流水计数器
CREATE TABLE IF NOT EXISTS dalian_certificate_daily_counters (
    cert_date_yymmdd VARCHAR(6) PRIMARY KEY COMMENT 'YYMMDD',
    next_seq INT NOT NULL DEFAULT 1 COMMENT '下一个可分配整数'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连质保书日流水计数';

-- 热处理及理化性能测试明细表（质保书核心数据源）
CREATE TABLE IF NOT EXISTS heat_treatment_test_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    heat_no VARCHAR(50) NOT NULL COMMENT '原材料炉号',
    batch_no VARCHAR(50) NOT NULL COMMENT '热处理批号',
    material_no VARCHAR(128) NOT NULL DEFAULT '' COMMENT '物料号',

    mech_test_temp VARCHAR(50) COMMENT '测试温度',
    mech_yield_strength VARCHAR(50) COMMENT '屈服强度',
    mech_tensile_strength VARCHAR(50) COMMENT '抗拉强度',
    mech_elongation VARCHAR(50) COMMENT '伸长率',
    mech_reduction_area VARCHAR(50) COMMENT '断面收缩率',
    mech_hardness VARCHAR(50) COMMENT '硬度',
    mech_impact_test VARCHAR(100) COMMENT '冲击试验',

    chem_raw_c DECIMAL(8,4) COMMENT '原材C',
    chem_raw_mn DECIMAL(8,4) COMMENT '原材Mn',
    chem_raw_p DECIMAL(8,4) COMMENT '原材P',
    chem_raw_s DECIMAL(8,4) COMMENT '原材S',
    chem_raw_si DECIMAL(8,4) COMMENT '原材Si',
    chem_raw_cu DECIMAL(8,4) COMMENT '原材Cu',
    chem_raw_ni DECIMAL(8,4) COMMENT '原材Ni',
    chem_raw_cr DECIMAL(8,4) COMMENT '原材Cr',
    chem_raw_mo DECIMAL(8,4) COMMENT '原材Mo',
    chem_raw_v DECIMAL(8,4) COMMENT '原材V',
    chem_raw_ceq DECIMAL(8,4) COMMENT '原材CEQ',

    chem_self_c DECIMAL(8,4) COMMENT '自检C',
    chem_self_mn DECIMAL(8,4) COMMENT '自检Mn',
    chem_self_p DECIMAL(8,4) COMMENT '自检P',
    chem_self_s DECIMAL(8,4) COMMENT '自检S',
    chem_self_si DECIMAL(8,4) COMMENT '自检Si',
    chem_self_cu DECIMAL(8,4) COMMENT '自检Cu',
    chem_self_ni DECIMAL(8,4) COMMENT '自检Ni',
    chem_self_cr DECIMAL(8,4) COMMENT '自检Cr',
    chem_self_mo DECIMAL(8,4) COMMENT '自检Mo',
    chem_self_v DECIMAL(8,4) COMMENT '自检V',
    chem_self_ceq DECIMAL(8,4) COMMENT '自检CEQ',
    test_result VARCHAR(16) COMMENT '测试结果',
    remark VARCHAR(255) COMMENT '备注',

    created_by VARCHAR(50) COMMENT '录入人',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_heat_batch_material (heat_no, batch_no, material_no),
    INDEX idx_heat_no (heat_no),
    INDEX idx_batch_no (batch_no),
    INDEX idx_material_no (material_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='热处理及理化性能测试明细表';

CREATE TABLE IF NOT EXISTS heat_treatment_trial_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    test_date VARCHAR(8) COMMENT '日期 yyyymmdd',
    heat_no VARCHAR(50) NOT NULL COMMENT '炉号',
    batch_no VARCHAR(50) NOT NULL COMMENT '热处理批号',
    spec_model VARCHAR(255) COMMENT '规格型号',
    mech_yield_strength VARCHAR(50) NOT NULL COMMENT '屈服标准',
    mech_tensile_strength VARCHAR(50) NOT NULL COMMENT '抗拉标准',
    mech_elongation VARCHAR(50) NOT NULL COMMENT '延伸标准',
    mech_reduction_area VARCHAR(50) NOT NULL COMMENT 'Z标准',
    mech_hardness_1 VARCHAR(50) NOT NULL COMMENT '硬度1',
    mech_hardness_2 VARCHAR(50) NOT NULL COMMENT '硬度2',
    mech_hardness_3 VARCHAR(50) NOT NULL COMMENT '硬度3',
    mech_impact_test VARCHAR(100) NOT NULL COMMENT '-20℃下冲击标准',
    seq_no VARCHAR(50) COMMENT '序号',
    supplier_hardness VARCHAR(100) COMMENT '热处理厂硬度',
    material_no VARCHAR(128) COMMENT '物料号',
    remark VARCHAR(255) COMMENT '备注',
    material VARCHAR(64) COMMENT '材料',
    updated_by VARCHAR(50) NOT NULL COMMENT '更新者',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_trial_heat_batch (heat_no, batch_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='试验记录表';

CREATE TABLE IF NOT EXISTS heat_treatment_chemical_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    heat_no VARCHAR(50) NOT NULL COMMENT '炉号',
    chem_raw_c DECIMAL(8,4) COMMENT '原材 碳 C(%)',
    chem_raw_mn DECIMAL(8,4) COMMENT '原材 锰 Mn(%)',
    chem_raw_p DECIMAL(8,4) COMMENT '原材 磷 P(%)',
    chem_raw_s DECIMAL(8,4) COMMENT '原材 硫 S(%)',
    chem_raw_si DECIMAL(8,4) COMMENT '原材 硅 Si(%)',
    chem_raw_cu DECIMAL(8,4) COMMENT '原材 铜 Cu(%)',
    chem_raw_ni DECIMAL(8,4) COMMENT '原材 镍 Ni(%)',
    chem_raw_cr DECIMAL(8,4) COMMENT '原材 铬 Cr(%)',
    chem_raw_mo DECIMAL(8,4) COMMENT '原材 钼 Mo(%)',
    chem_raw_v DECIMAL(8,4) COMMENT '原材 钒 V(%)',
    chem_raw_ceq DECIMAL(8,4) COMMENT '原材 碳当量 CEQ',
    chem_self_c DECIMAL(8,4) COMMENT '自检 碳 C(%)',
    chem_self_mn DECIMAL(8,4) COMMENT '自检 锰 Mn(%)',
    chem_self_p DECIMAL(8,4) COMMENT '自检 磷 P(%)',
    chem_self_s DECIMAL(8,4) COMMENT '自检 硫 S(%)',
    chem_self_si DECIMAL(8,4) COMMENT '自检 硅 Si(%)',
    chem_self_cu DECIMAL(8,4) COMMENT '自检 铜 Cu(%)',
    chem_self_ni DECIMAL(8,4) COMMENT '自检 镍 Ni(%)',
    chem_self_cr DECIMAL(8,4) COMMENT '自检 铬 Cr(%)',
    chem_self_mo DECIMAL(8,4) COMMENT '自检 钼 Mo(%)',
    chem_self_v DECIMAL(8,4) COMMENT '自检 钒 V(%)',
    chem_self_ceq DECIMAL(8,4) COMMENT '自检 碳当量 CEQ',
    updated_by VARCHAR(50) NOT NULL COMMENT '更新者',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_chemical_heat_no (heat_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='化学成分表';

-- 大连打包发货明细表
CREATE TABLE IF NOT EXISTS dalian_shipping_details (
    id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '系统唯一流水号ID',
    delivery_date VARCHAR(8) NOT NULL COMMENT '发货日期 (YYYYMMDD，如 20260401)',
    packing_no VARCHAR(50) NULL COMMENT '包装序号 (如 1, 2, 3...)',
    order_no VARCHAR(100) NOT NULL COMMENT '订单号 (可能包含括号，如 4511615593(2))',
    factory_order_no VARCHAR(128) NULL COMMENT '本厂单号',
    item_no VARCHAR(50) NOT NULL COMMENT '条目 (行号)',
    drawing_no VARCHAR(255) NULL COMMENT '图纸号',
    ap1_material_no VARCHAR(100) NULL COMMENT 'AP1料号',
    r3p_material_no VARCHAR(100) NULL COMMENT 'R3P物料号',
    spec_model VARCHAR(255) NULL COMMENT '规格型号',
    material VARCHAR(100) NULL COMMENT '材质',
    quantity INT DEFAULT 0 COMMENT '数量 (本次发货数量)',
    unit_weight DECIMAL(10,3) DEFAULT 0.000 COMMENT '单重 (单件产品净重 kg)',
    total_weight DECIMAL(10,3) DEFAULT 0.000 COMMENT '总重 (Gross weight = quantity * unit_weight)',
    unit_price DECIMAL(10,2) NULL COMMENT '单价',
    remark TEXT NULL COMMENT '备注 (系统备注)',
    buyer VARCHAR(100) NULL COMMENT '采购员',
    heat_no VARCHAR(100) NULL COMMENT '炉号 (Heat No)',
    heat_treatment_batch_no VARCHAR(100) NULL COMMENT '热处理批号',
    shipping_mark_remark VARCHAR(255) NULL COMMENT '唛头备注 (如: 绿色, 白色)',
    created_by VARCHAR(50) NULL COMMENT '操作人',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_delivery_date (delivery_date),
    INDEX idx_order_item (order_no, item_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大连打包发货明细表';

-- 插入默认角色
INSERT IGNORE INTO roles (id, code, name_zh, name_en, name_ja) VALUES
(1, 'super_admin', '董事长', 'Super Admin', '取締役'),
(2, 'general_manager', '总经理', 'General Manager', '総経理'),
(3, 'finance', '财务主管/出纳', 'Finance', '財務'),
(4, 'sales', '销售/业务员', 'Sales', '営業'),
(5, 'purchasing', '采购/外协专员', 'Purchasing', '調達'),
(6, 'production_manager', '生产主管/车间主任', 'Production Manager', '製造責任者'),
(7, 'qc', '质检员', 'QC', '品質検査'),
(8, 'warehouse', '库管/包装发货员', 'Warehouse/Shipping', '倉庫/発送');

INSERT IGNORE INTO document_templates (id, doc_type, language, version, template_name, content, is_active) VALUES
(1, 'quality_certificate', 'zh-CN', 'v1.0', '质保书-默认模板', '质保书默认文本模板(v1.0)', 1),
(2, 'packing_list', 'zh-CN', 'v1.0', '装箱单-默认模板', '装箱单默认文本模板(v1.0)', 1),
(3, 'shipping_mark', 'zh-CN', 'v1.0', '唛头-默认模板', '唛头默认文本模板(v1.0)', 1);
