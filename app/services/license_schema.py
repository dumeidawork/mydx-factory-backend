"""许可证模块建表语句（与质检表解耦）。"""

CREATE_LICENSE_DOCS_SQL = """
CREATE TABLE IF NOT EXISTS license_docs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL COMMENT '法国订单号',
    source VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'packing / order_details',
    inspector_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '检查责任者',
    batch_date DATE NULL COMMENT '批量年月日',
    pad_item_no TINYINT NOT NULL DEFAULT 0 COMMENT '条目是否五位补零',
    contract_amount DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '整单含税总价',
    contract_weight DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '整单总重',
    cs_amount DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '碳钢含税总价',
    cs_weight DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '碳钢总重',
    cs_unit_price DECIMAL(12,4) NULL COMMENT '碳钢单价',
    ss_amount DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '不锈钢含税总价',
    ss_weight DECIMAL(16,4) NOT NULL DEFAULT 0 COMMENT '不锈钢总重',
    ss_unit_price DECIMAL(12,4) NULL COMMENT '不锈钢单价',
    generated_by VARCHAR(64) NOT NULL DEFAULT '',
    generated_by_user_id INT NULL,
    generated_at DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_license_docs_order (order_no),
    INDEX idx_license_docs_generated (generated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='许可证单据头'
"""

CREATE_LICENSE_CERTIFICATE_ITEMS_SQL = """
CREATE TABLE IF NOT EXISTS license_certificate_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_no VARCHAR(64) NOT NULL,
    grade VARCHAR(16) NOT NULL COMMENT 'A105 / 304 / 316',
    line_key VARCHAR(64) NOT NULL COMMENT 'p:{packing_id} / d:{detail_id}',
    spec_model VARCHAR(256) NOT NULL DEFAULT '',
    qty INT NOT NULL DEFAULT 0,
    chem_c VARCHAR(32) NOT NULL DEFAULT '',
    chem_si VARCHAR(32) NOT NULL DEFAULT '',
    chem_mn VARCHAR(32) NOT NULL DEFAULT '',
    chem_s VARCHAR(32) NOT NULL DEFAULT '',
    chem_p VARCHAR(32) NOT NULL DEFAULT '',
    chem_cr VARCHAR(32) NOT NULL DEFAULT '',
    chem_ni VARCHAR(32) NOT NULL DEFAULT '',
    chem_mo VARCHAR(32) NOT NULL DEFAULT '',
    chem_cu VARCHAR(32) NOT NULL DEFAULT '',
    mech_yield VARCHAR(16) NOT NULL DEFAULT '',
    mech_tensile VARCHAR(16) NOT NULL DEFAULT '',
    mech_elong VARCHAR(16) NOT NULL DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_license_cert_line (order_no, grade, line_key),
    INDEX idx_license_cert_order (order_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='许可证合格证行化学机械锁定'
"""
