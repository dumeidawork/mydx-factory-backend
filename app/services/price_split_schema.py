"""价格拆分表：法国 / 大连分表，结构对齐。"""
from __future__ import annotations

REGIONS = ("france", "dalian")
MATERIAL_GROUPS = ("A105", "SS")
REGION_TABLES = {
    "france": "price_split_france",
    "dalian": "price_split_dalian",
}

STRING_FIELDS = (
    "material_group",
    "drawing_no",
    "drawing_code",
    "drawing_rev",
    "material_no",
    "part_no",
    "ref_part_no",
    "ref_material_no",
    "description",
    "flange_type",
    "material",
    "scope",
    "award_yn",
    "sales_mode",
    "has_m16",
    "has_through_hole",
    "has_ptfe_hole",
    "price_version",
    "price_change_remark",
    "source_file",
    "source_sheet",
    "created_by",
    "updated_by",
)

INT_FIELDS = ("year_usage", "source_row", "replaces_id", "superseded_by_id")

PRICE_COMPARE_FIELDS = tuple(f for f in (
    "steel_price",
    "blanking_weight",
    "ht_blanking_weight",
    "forged_weight",
    "ht_loss",
    "gas_loss",
    "heat_loss",
    "net_weight",
    "recover_weight",
    "scrap_price",
    "blanking_cost",
    "forging_cost",
    "heat_treatment_cost",
    "ht_freight_cost",
    "ring_rolling_cost",
    "machining_cost",
    "ht_machining_extra",
    "drilling_cost",
    "grounding_hole_cost",
    "hoisting_hole_cost",
    "through_hole_cost",
    "ptfe_hole_cost",
    "m6_hole_cost",
    "packing_cost",
    "other_cost",
    "transport_cost",
    "port_surcharge",
    "profit_rate",
    "material_cost",
    "total_process_cost",
    "net_price",
    "vat_rate",
    "tax_inclusive_price",
))

NUMERIC_FIELDS = (
    "dn",
    "steel_price",
    "blanking_weight",
    "ht_blanking_weight",
    "forged_weight",
    "ht_loss",
    "gas_loss",
    "heat_loss",
    "net_weight",
    "recover_weight",
    "scrap_price",
    "blanking_cost",
    "forging_cost",
    "heat_treatment_cost",
    "ht_freight_cost",
    "ring_rolling_cost",
    "machining_cost",
    "ht_machining_extra",
    "drilling_cost",
    "grounding_hole_cost",
    "hoisting_hole_cost",
    "through_hole_cost",
    "ptfe_hole_cost",
    "m6_hole_cost",
    "packing_cost",
    "other_cost",
    "transport_cost",
    "port_surcharge",
    "profit_rate",
    "material_cost",
    "total_process_cost",
    "net_price",
    "excel_net_price",
    "vat_rate",
    "tax_inclusive_price",
)

DERIVED_FIELDS = (
    "ht_loss",
    "ht_blanking_weight",
    "forged_weight",
    "recover_weight",
    "material_cost",
    "total_process_cost",
    "net_price",
    "tax_inclusive_price",
)

INPUT_NUMERIC_FIELDS = tuple(f for f in NUMERIC_FIELDS if f not in DERIVED_FIELDS)

ALL_DATA_COLUMNS = (
    "material_group",
    "drawing_no",
    "drawing_code",
    "drawing_rev",
    "material_no",
    "part_no",
    "ref_part_no",
    "ref_material_no",
    "description",
    "flange_type",
    "dn",
    "material",
    "scope",
    "award_yn",
    "sales_mode",
    "year_usage",
    "has_m16",
    "has_through_hole",
    "has_ptfe_hole",
    "steel_price",
    "blanking_weight",
    "ht_blanking_weight",
    "forged_weight",
    "ht_loss",
    "gas_loss",
    "heat_loss",
    "net_weight",
    "recover_weight",
    "scrap_price",
    "blanking_cost",
    "forging_cost",
    "heat_treatment_cost",
    "ht_freight_cost",
    "ring_rolling_cost",
    "machining_cost",
    "ht_machining_extra",
    "drilling_cost",
    "grounding_hole_cost",
    "hoisting_hole_cost",
    "through_hole_cost",
    "ptfe_hole_cost",
    "m6_hole_cost",
    "packing_cost",
    "other_cost",
    "transport_cost",
    "port_surcharge",
    "profit_rate",
    "material_cost",
    "total_process_cost",
    "net_price",
    "excel_net_price",
    "vat_rate",
    "tax_inclusive_price",
    "price_version",
    "price_change_remark",
    "replaces_id",
    "source_file",
    "source_sheet",
    "source_row",
)

SELECT_SQL = ", ".join(
    (
        "id",
        *ALL_DATA_COLUMNS,
        "created_by",
        "created_by_user_id",
        "created_at",
        "updated_by",
        "updated_by_user_id",
        "updated_at",
        "is_valid",
        "valid_slot",
        "superseded_by_id",
    )
)

CREATE_LOG_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS price_split_operation_logs (
            id BIGINT AUTO_INCREMENT PRIMARY KEY,
            region VARCHAR(16) NOT NULL COMMENT 'france/dalian',
            row_id BIGINT NULL COMMENT '业务行ID，删除后可空',
            material_group VARCHAR(8) NULL,
            material_no VARCHAR(100) NULL,
            action VARCHAR(32) NOT NULL COMMENT 'create/update/upload/delete',
            operator_user_id INT NULL,
            operator_name VARCHAR(64) NOT NULL DEFAULT '',
            operated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            change_summary TEXT NULL COMMENT '变更内容 JSON',
            INDEX idx_ps_log_region_row (region, row_id),
            INDEX idx_ps_log_material (region, material_no),
            INDEX idx_ps_log_action (action),
            INDEX idx_ps_log_time (operated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='价格拆分表操作日志'
    """

VERSION_COLUMN_ALTERS = (
    (
        "is_valid",
        "ALTER TABLE {table} ADD COLUMN is_valid TINYINT NOT NULL DEFAULT 1 COMMENT '1有效 0无效历史' AFTER updated_at",
    ),
    (
        "valid_slot",
        "ALTER TABLE {table} ADD COLUMN valid_slot TINYINT NULL COMMENT '有效行=1，无效行=NULL' AFTER is_valid",
    ),
    (
        "price_change_remark",
        "ALTER TABLE {table} ADD COLUMN price_change_remark VARCHAR(500) NULL COMMENT '调价/建档说明' AFTER price_version",
    ),
    (
        "replaces_id",
        "ALTER TABLE {table} ADD COLUMN replaces_id BIGINT NULL COMMENT '本行替换的旧行ID' AFTER price_change_remark",
    ),
    (
        "superseded_by_id",
        "ALTER TABLE {table} ADD COLUMN superseded_by_id BIGINT NULL COMMENT '被哪条新行替代' AFTER replaces_id",
    ),
)

VAT_COLUMN_ALTERS = (
    (
        "vat_rate",
        "ALTER TABLE {table} ADD COLUMN vat_rate DECIMAL(8,4) NULL COMMENT '增值税%，如13表示13%' AFTER excel_net_price",
    ),
    (
        "tax_inclusive_price",
        "ALTER TABLE {table} ADD COLUMN tax_inclusive_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '含税单价=最终报价*(1+增值税/100)' AFTER vat_rate",
    ),
)

UNIQUE_INDEX_SQL = {
    "france": "ALTER TABLE price_split_france ADD UNIQUE KEY uk_valid_identity (material_group, drawing_no, material_no, valid_slot)",
    "dalian": "ALTER TABLE price_split_dalian ADD UNIQUE KEY uk_valid_identity (material_group, drawing_no, material_no, part_no, valid_slot)",
}


def identity_clause(region: str) -> str:
    clause = "material_group=%s AND COALESCE(drawing_no,'')=%s AND material_no=%s"
    if region == "dalian":
        clause += " AND COALESCE(part_no,'')=%s"
    return clause + " AND is_valid=1"


def identity_values(region: str, item: dict) -> tuple:
    values = (
        item.get("material_group") or "A105",
        (item.get("drawing_no") or "").strip(),
        (item.get("material_no") or "").strip(),
    )
    if region == "dalian":
        return (*values, (item.get("part_no") or "").strip())
    return values


AUDIT_COLUMN_ALTERS = (
    (
        "created_by_user_id",
        "ALTER TABLE {table} ADD COLUMN created_by_user_id INT NULL COMMENT '上传者用户ID' AFTER created_by",
    ),
    (
        "updated_by_user_id",
        "ALTER TABLE {table} ADD COLUMN updated_by_user_id INT NULL COMMENT '更改者用户ID' AFTER updated_by",
    ),
)

# 源表没有、当前 Tab 不应落库的字段。两表结构相同，靠这里按区域+材质组清零。
_CLEAR_STRING = (
    "part_no",
    "ref_part_no",
    "ref_material_no",
    "award_yn",
    "sales_mode",
    "has_m16",
    "has_through_hole",
    "has_ptfe_hole",
    "scope",
)
_CLEAR_NUMERIC = (
    "port_surcharge",
    "grounding_hole_cost",
    "through_hole_cost",
    "m6_hole_cost",
    "heat_treatment_cost",
    "ht_freight_cost",
    "ht_machining_extra",
    "gas_loss",
    "ht_loss",
    "ht_blanking_weight",
    "heat_loss",
    "vat_rate",
    "tax_inclusive_price",
)

INACTIVE_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("france", "A105"): (
        "part_no",
        "ref_part_no",
        "ref_material_no",
        "award_yn",
        "sales_mode",
        "has_m16",
        "has_through_hole",
        "has_ptfe_hole",
        "scope",
        "grounding_hole_cost",
        "through_hole_cost",
        "heat_loss",
    ),
    ("france", "SS"): (
        "part_no",
        "ref_part_no",
        "ref_material_no",
        "award_yn",
        "sales_mode",
        "has_m16",
        "has_through_hole",
        "has_ptfe_hole",
        "scope",
        "grounding_hole_cost",
        "through_hole_cost",
        "heat_treatment_cost",
        "ht_freight_cost",
        "ht_machining_extra",
        "gas_loss",
        "ht_loss",
        "ht_blanking_weight",
    ),
    ("dalian", "A105"): (
        "port_surcharge",
        "m6_hole_cost",
        "through_hole_cost",
        "ref_part_no",
        "ref_material_no",
        "has_m16",
        "has_through_hole",
        "has_ptfe_hole",
        "scope",
        "heat_loss",
        "vat_rate",
        "tax_inclusive_price",
    ),
    ("dalian", "SS"): (
        "port_surcharge",
        "award_yn",
        "sales_mode",
        "grounding_hole_cost",
        "heat_treatment_cost",
        "ht_freight_cost",
        "ht_machining_extra",
        "gas_loss",
        "ht_loss",
        "ht_blanking_weight",
        "vat_rate",
        "tax_inclusive_price",
    ),
}


def sanitize_inactive_fields(item: dict, region: str) -> None:
    """按法国/大连源表清掉当前材质组不存在的字段。"""
    group = (item.get("material_group") or "A105").upper()
    if group not in MATERIAL_GROUPS:
        group = "A105"
    key = ((region or "").strip().lower(), group)
    for field in INACTIVE_FIELDS.get(key, ()):
        if field in _CLEAR_STRING:
            item[field] = ""
        elif field in _CLEAR_NUMERIC:
            item[field] = 0
        elif field in INT_FIELDS:
            item[field] = 0


def _create_table_sql(table_name: str, comment: str, unique_key: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '内部流水号',
            material_group VARCHAR(8) NOT NULL COMMENT 'A105 或 SS',
            drawing_no VARCHAR(255) NULL COMMENT '图纸号(可含版本)',
            drawing_code VARCHAR(128) NULL COMMENT '纯图纸号',
            drawing_rev VARCHAR(32) NULL COMMENT '图纸版本',
            material_no VARCHAR(100) NOT NULL COMMENT 'R3P 物料号',
            part_no VARCHAR(100) NULL COMMENT 'AP1 料号',
            ref_part_no VARCHAR(100) NULL COMMENT '对照 AP1(大连不锈钢左侧)',
            ref_material_no VARCHAR(100) NULL COMMENT '对照 R3P(大连不锈钢左侧)',
            description VARCHAR(512) NULL COMMENT '描述',
            flange_type VARCHAR(32) NULL COMMENT '法兰类型',
            dn DECIMAL(10,2) NULL COMMENT 'DN',
            material VARCHAR(32) NULL COMMENT '材质 A105/304/316',
            scope VARCHAR(64) NULL COMMENT '范围 PTFE/新增等',
            award_yn VARCHAR(8) NULL COMMENT 'Award Y/N',
            sales_mode VARCHAR(32) NULL COMMENT '寄售/标准',
            year_usage INT NULL COMMENT '年用量',
            has_m16 VARCHAR(8) NULL COMMENT 'M16 Y/N',
            has_through_hole VARCHAR(8) NULL COMMENT '通孔 Y/N',
            has_ptfe_hole VARCHAR(8) NULL COMMENT 'PTFE孔 Y/N',
            steel_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '钢材价格 元/吨',
            blanking_weight DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '不含热处理下料毛重 kg',
            ht_blanking_weight DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '增加热处理下料毛重 kg',
            forged_weight DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '锻造后毛重 kg',
            ht_loss DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '热处理火耗',
            gas_loss DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '天然气加热火耗(A105)',
            heat_loss DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '火耗(不锈钢)',
            net_weight DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '净重 kg',
            recover_weight DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '可回收料重量 kg',
            scrap_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '废料回收价格 元/吨',
            blanking_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '下料费用 元/吨',
            forging_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '锻造费用 元/吨',
            heat_treatment_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '热处理费用 元/吨',
            ht_freight_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '外协热处理运费 元/吨',
            ring_rolling_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '碾环费用 元/吨',
            machining_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '机加工费用 元/吨',
            ht_machining_extra DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '热处理机加工增加费用 元/吨',
            drilling_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '钻孔费用 元/吨',
            grounding_hole_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '打孔接地费用 元/吨',
            hoisting_hole_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '吊装孔费用 元/吨',
            through_hole_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '通孔费用 元/吨',
            ptfe_hole_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT 'PTFE钻孔费用 元/吨',
            m6_hole_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT 'M6侧孔费用 元/吨',
            packing_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '包装费用 元/吨',
            other_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '其他成本 元/吨',
            transport_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '运输费用 元/吨',
            port_surcharge DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '港杂费用 元/吨',
            profit_rate DECIMAL(12,6) NOT NULL DEFAULT 1.070000 COMMENT '利润率',
            material_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '材料成本 元/件',
            total_process_cost DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '总加工成本 元/件',
            net_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '最终报价 元/件',
            excel_net_price DECIMAL(18,8) NULL COMMENT 'Excel缓存最终价',
            vat_rate DECIMAL(8,4) NULL COMMENT '增值税%，如13表示13%',
            tax_inclusive_price DECIMAL(18,8) NOT NULL DEFAULT 0 COMMENT '含税单价',
            price_version VARCHAR(32) NOT NULL DEFAULT '' COMMENT '价格版本如 2025-04-01',
            price_change_remark VARCHAR(500) NULL COMMENT '调价/建档说明',
            replaces_id BIGINT NULL COMMENT '本行替换的旧行ID',
            source_file VARCHAR(255) NULL,
            source_sheet VARCHAR(128) NULL,
            source_row INT NULL,
            created_by VARCHAR(50) NULL COMMENT '上传者/录入人姓名',
            created_by_user_id INT NULL COMMENT '上传者用户ID',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '上传时间',
            updated_by VARCHAR(50) NULL COMMENT '更改者姓名',
            updated_by_user_id INT NULL COMMENT '更改者用户ID',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更改时间',
            is_valid TINYINT NOT NULL DEFAULT 1 COMMENT '1有效 0无效历史',
            valid_slot TINYINT NULL COMMENT '有效行=1，无效行=NULL',
            superseded_by_id BIGINT NULL COMMENT '被哪条新行替代',
            UNIQUE KEY uk_valid_identity {unique_key},
            INDEX idx_drawing_no (drawing_no),
            INDEX idx_part_no (part_no),
            INDEX idx_material (material),
            INDEX idx_is_valid (is_valid)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='{comment}'
    """


CREATE_TABLE_SQL = {
    "france": _create_table_sql(
        "price_split_france",
        "法国价格拆分表",
        "(material_group, drawing_no, material_no, valid_slot)",
    ),
    "dalian": _create_table_sql(
        "price_split_dalian",
        "大连价格拆分表",
        "(material_group, drawing_no, material_no, part_no, valid_slot)",
    ),
}
