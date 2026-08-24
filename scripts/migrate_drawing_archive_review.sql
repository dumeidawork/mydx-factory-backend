-- 图纸建档：标准号/图纸号拆分、尺寸、审核状态、物料三列（可对已有库单独执行）
SET NAMES utf8mb4;
USE mingyuan_erp;

ALTER TABLE drawing_archives
    ADD COLUMN IF NOT EXISTS standard_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '标准号' AFTER drawing_no,
    ADD COLUMN IF NOT EXISTS drawing_rev_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '图纸号-版本号（Rev 形）' AFTER standard_no,
    ADD COLUMN IF NOT EXISTS spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格' AFTER spec_model,
    ADD COLUMN IF NOT EXISTS model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号' AFTER spec,
    ADD COLUMN IF NOT EXISTS material_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '物料号' AFTER model,
    ADD COLUMN IF NOT EXISTS od_d VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'D外径' AFTER material_no,
    ADD COLUMN IF NOT EXISTS id_c VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'C内径' AFTER od_d,
    ADD COLUMN IF NOT EXISTS thk_t VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'T厚度' AFTER id_c,
    ADD COLUMN IF NOT EXISTS thk_f VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'F总厚度' AFTER thk_t,
    ADD COLUMN IF NOT EXISTS mouth_m VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'M上口' AFTER thk_f,
    ADD COLUMN IF NOT EXISTS center_k VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'K中心距' AFTER mouth_m,
    ADD COLUMN IF NOT EXISTS seat_a VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'A台径' AFTER center_k,
    ADD COLUMN IF NOT EXISTS seat_h_f VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'f台高' AFTER seat_a,
    ADD COLUMN IF NOT EXISTS r_val VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'R' AFTER seat_h_f,
    ADD COLUMN IF NOT EXISTS hole_h VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'H孔径' AFTER r_val,
    ADD COLUMN IF NOT EXISTS hole_n VARCHAR(64) NOT NULL DEFAULT '' COMMENT 'N孔数' AFTER hole_h,
    ADD COLUMN IF NOT EXISTS side_b VARCHAR(64) NOT NULL DEFAULT '' COMMENT '侧孔B' AFTER hole_n,
    ADD COLUMN IF NOT EXISTS side_b_depth VARCHAR(64) NOT NULL DEFAULT '' COMMENT '侧孔B深度' AFTER side_b,
    ADD COLUMN IF NOT EXISTS unit_weight VARCHAR(64) NOT NULL DEFAULT '' COMMENT '单重' AFTER side_b_depth,
    ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT '审核' COMMENT '上传/修改/审核' AFTER unit_weight,
    ADD COLUMN IF NOT EXISTS reviewer_user_id INT NULL COMMENT '审核负责人' AFTER status,
    ADD COLUMN IF NOT EXISTS reviewer_name VARCHAR(64) NOT NULL DEFAULT '' COMMENT '审核负责人姓名' AFTER reviewer_user_id,
    ADD COLUMN IF NOT EXISTS review_remark TEXT NULL COMMENT '不一致说明' AFTER reviewer_name,
    ADD COLUMN IF NOT EXISTS reviewed_at DATETIME NULL COMMENT '最近确认时间' AFTER review_remark;

ALTER TABLE drawing_archives
    ADD INDEX IF NOT EXISTS idx_drawing_status (status),
    ADD INDEX IF NOT EXISTS idx_drawing_reviewer (reviewer_user_id),
    ADD INDEX IF NOT EXISTS idx_drawing_standard_no (standard_no),
    ADD INDEX IF NOT EXISTS idx_drawing_rev_no (drawing_rev_no);

UPDATE drawing_archives
SET status = '审核'
WHERE status IS NULL OR TRIM(status) = '';

UPDATE drawing_archives
SET drawing_rev_no = drawing_no
WHERE COALESCE(standard_no, '') = ''
  AND COALESCE(drawing_rev_no, '') = ''
  AND (drawing_no LIKE '% Rev%' OR drawing_no LIKE '%Rev%' OR drawing_no REGEXP '-[0-9]+$');

UPDATE drawing_archives
SET standard_no = drawing_no
WHERE COALESCE(standard_no, '') = ''
  AND COALESCE(drawing_rev_no, '') = '';

ALTER TABLE materials
    ADD COLUMN IF NOT EXISTS standard_no VARCHAR(255) NOT NULL DEFAULT '' COMMENT '标准号' AFTER drawing_no,
    ADD COLUMN IF NOT EXISTS spec VARCHAR(64) NOT NULL DEFAULT '' COMMENT '规格' AFTER spec_model,
    ADD COLUMN IF NOT EXISTS model VARCHAR(64) NOT NULL DEFAULT '' COMMENT '型号' AFTER spec;
