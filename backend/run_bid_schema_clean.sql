-- ============================================================
-- 清理旧表（如有）
-- ============================================================
BEGIN;

DROP TABLE IF EXISTS app.bid_report CASCADE;
DROP TABLE IF EXISTS app.bid_risk CASCADE;
DROP TABLE IF EXISTS app.bid_task_log CASCADE;
DROP TABLE IF EXISTS app.bid_document_tag CASCADE;
DROP TABLE IF EXISTS app.bid_doc_chunk CASCADE;
DROP TABLE IF EXISTS app.bid_document CASCADE;
DROP TABLE IF EXISTS app.competitor_history CASCADE;
DROP TABLE IF EXISTS app.enterprise_profile CASCADE;
DROP TABLE IF EXISTS app.bid_tag_relation CASCADE;
DROP TABLE IF EXISTS app.bid_tag_dict CASCADE;
DROP TABLE IF EXISTS app.bid_tag_category CASCADE;
DROP TABLE IF EXISTS app.bid_tag_level CASCADE;

-- ============================================================
-- 建表（从 ORM 模型生成）
-- ============================================================

CREATE TABLE app.bid_tag_category (
    category_code VARCHAR(20) NOT NULL,
    category_name VARCHAR(100) NOT NULL,
    category_desc TEXT,
    sort_order INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL,
    PRIMARY KEY (category_code)
);

CREATE TABLE app.bid_tag_level (
    level_code VARCHAR(10) NOT NULL,
    level_name VARCHAR(50) NOT NULL,
    level_desc TEXT,
    sort_order INTEGER NOT NULL,
    PRIMARY KEY (level_code)
);

CREATE TABLE app.bid_tag_dict (
    tag_id BIGSERIAL NOT NULL,
    tag_code VARCHAR(80) NOT NULL,
    tag_name VARCHAR(200) NOT NULL,
    tag_value VARCHAR(500),
    category_code VARCHAR(20),
    level_code VARCHAR(10),
    is_active BOOLEAN NOT NULL,
    data_type VARCHAR(20) NOT NULL,
    extraction_prompt TEXT,
    value_example TEXT,
    priority VARCHAR(10) NOT NULL,
    PRIMARY KEY (tag_id),
    UNIQUE (tag_code),
    FOREIGN KEY(category_code) REFERENCES app.bid_tag_category (category_code),
    FOREIGN KEY(level_code) REFERENCES app.bid_tag_level (level_code)
);

CREATE TABLE app.bid_tag_relation (
    relation_id BIGSERIAL NOT NULL,
    source_tag_code VARCHAR(80) NOT NULL,
    target_tag_code VARCHAR(80) NOT NULL,
    relation_type VARCHAR(30) NOT NULL,
    relation_desc TEXT,
    rule_json JSONB,
    priority VARCHAR(10) NOT NULL,
    is_active BOOLEAN NOT NULL,
    PRIMARY KEY (relation_id)
);

CREATE TABLE app.bid_document (
    doc_id BIGSERIAL NOT NULL,
    doc_name VARCHAR(500) NOT NULL,
    doc_type VARCHAR(30),
    doc_url TEXT,
    project_code VARCHAR(100),
    file_hash VARCHAR(64),
    parse_status VARCHAR(20) NOT NULL,
    raw_text_path TEXT,
    PRIMARY KEY (doc_id)
);

CREATE TABLE app.bid_doc_chunk (
    chunk_id BIGSERIAL NOT NULL,
    doc_id BIGINT NOT NULL,
    chunk_index INTEGER NOT NULL,
    page_no INTEGER,
    section_path TEXT,
    chunk_text TEXT NOT NULL,
    chunk_type VARCHAR(20) NOT NULL,
    category_codes VARCHAR[],
    candidate_tags VARCHAR[],
    prev_chunk_id BIGINT,
    next_chunk_id BIGINT,
    PRIMARY KEY (chunk_id),
    CONSTRAINT uq_bid_doc_chunk_doc_index UNIQUE (doc_id, chunk_index),
    FOREIGN KEY(doc_id) REFERENCES app.bid_document (doc_id)
);

CREATE TABLE app.bid_document_tag (
    id BIGSERIAL NOT NULL,
    doc_id BIGINT NOT NULL,
    tag_id BIGINT NOT NULL,
    tag_value TEXT,
    tag_value_json JSONB,
    source_text TEXT,
    source_chunk_id BIGINT,
    source_page INTEGER,
    confidence NUMERIC(5, 2),
    extract_method VARCHAR(20),
    llm_model VARCHAR(50),
    extracted_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reviewed BOOLEAN NOT NULL,
    reviewer VARCHAR(100),
    reviewed_at TIMESTAMP WITH TIME ZONE,
    remark TEXT,
    PRIMARY KEY (id),
    CONSTRAINT uq_bid_doc_tag UNIQUE (doc_id, tag_id),
    FOREIGN KEY(doc_id) REFERENCES app.bid_document (doc_id),
    FOREIGN KEY(tag_id) REFERENCES app.bid_tag_dict (tag_id)
);

CREATE TABLE app.bid_task_log (
    task_id BIGSERIAL NOT NULL,
    doc_id BIGINT NOT NULL,
    thread_id VARCHAR(100),
    stage VARCHAR(50) NOT NULL,
    node_name VARCHAR(50),
    status VARCHAR(20) NOT NULL,
    attempt INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE,
    finished_at TIMESTAMP WITH TIME ZONE,
    duration_ms INTEGER,
    input_summary TEXT,
    output_summary TEXT,
    error_msg TEXT,
    payload JSONB,
    PRIMARY KEY (task_id),
    FOREIGN KEY(doc_id) REFERENCES app.bid_document (doc_id)
);

CREATE TABLE app.bid_risk (
    risk_id BIGSERIAL NOT NULL,
    doc_id BIGINT NOT NULL,
    risk_type VARCHAR(40) NOT NULL,
    risk_level VARCHAR(10) NOT NULL,
    risk_title VARCHAR(500) NOT NULL,
    risk_desc TEXT,
    related_tags VARCHAR[],
    source_chunks BIGINT[],
    suggestion TEXT,
    confidence NUMERIC(5, 2),
    PRIMARY KEY (risk_id),
    FOREIGN KEY(doc_id) REFERENCES app.bid_document (doc_id)
);

CREATE TABLE app.bid_report (
    report_id BIGSERIAL NOT NULL,
    doc_id BIGINT NOT NULL,
    decision VARCHAR(20),
    overall_score NUMERIC(5, 2),
    qualification_score NUMERIC(5, 2),
    risk_score NUMERIC(5, 2),
    trap_score NUMERIC(5, 2),
    competition_score NUMERIC(5, 2),
    summary TEXT,
    report_md TEXT,
    report_json JSONB,
    PRIMARY KEY (report_id),
    UNIQUE (doc_id),
    FOREIGN KEY(doc_id) REFERENCES app.bid_document (doc_id)
);

CREATE TABLE app.enterprise_profile (
    ep_id BIGSERIAL NOT NULL,
    enterprise_name VARCHAR(200) NOT NULL,
    credit_code VARCHAR(18),
    qualifications JSONB,
    past_projects JSONB,
    financials JSONB,
    personnel JSONB,
    awards JSONB,
    blacklist_status JSONB,
    PRIMARY KEY (ep_id),
    UNIQUE (credit_code)
);

CREATE TABLE app.competitor_history (
    comp_id BIGSERIAL NOT NULL,
    enterprise_name VARCHAR(200) NOT NULL,
    project_name VARCHAR(500),
    project_amount NUMERIC(15, 2),
    bid_amount NUMERIC(15, 2),
    win BOOLEAN,
    bid_date DATE,
    project_type VARCHAR(50),
    region VARCHAR(50),
    PRIMARY KEY (comp_id)
);

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX ix_bid_doc_chunk_doc_id ON app.bid_doc_chunk(doc_id);
CREATE INDEX ix_bid_document_tag_doc_id ON app.bid_document_tag(doc_id);
CREATE INDEX ix_bid_document_tag_tag_id ON app.bid_document_tag(tag_id);
CREATE INDEX ix_bid_risk_doc_id ON app.bid_risk(doc_id);
CREATE INDEX ix_bid_task_log_doc_stage ON app.bid_task_log(doc_id, stage);
CREATE INDEX ix_bid_task_log_thread ON app.bid_task_log(thread_id);
CREATE INDEX ix_competitor_history_name ON app.competitor_history(enterprise_name);
CREATE INDEX ix_competitor_history_project ON app.competitor_history(project_type, region);
CREATE INDEX ix_enterprise_profile_name ON app.enterprise_profile(enterprise_name);

-- ============================================================
-- 种子数据
-- ============================================================
INSERT INTO app.bid_tag_level (level_code, level_name, sort_order) VALUES
('P0', '否决项', 1), ('P1', '关键项', 2), ('P2', '参考项', 3);

INSERT INTO app.bid_tag_category (category_code, category_name, category_desc, sort_order, is_active) VALUES
('CAT01', '项目基本信息', '项目名称、编号、类型等核心信息', 1, true),
('CAT02', '招标人信息', '招标人名称、联系方式', 2, true),
('CAT03', '预算与资金', '预算金额、付款方式、资金来源', 3, true),
('CAT04', '时间安排', '报名、答疑、开标时间', 4, true),
('CAT05', '资质要求', '企业资质、人员要求', 5, true),
('CAT06', '业绩要求', '类似项目业绩要求', 6, true),
('CAT07', '技术规格', '技术参数、技术方案要求', 7, true),
('CAT08', '合同条款', '履约保函、违约条款', 8, true),
('CAT09', '评标办法', '评分标准、定标方式', 9, true),
('CAT10', '风险条款', '萝卜坑、废标风险条款', 10, true),
('CAT11', '否决条款', '直接废标条款', 11, true);

INSERT INTO app.bid_tag_dict (tag_code, tag_name, category_code, level_code, is_active, data_type, extraction_prompt, priority) VALUES
-- CAT01
('PROJECT_NAME', '项目名称', 'CAT01', 'P0', true, 'str', '招标项目的完整名称', 'P0'),
('PROJECT_CODE', '项目编号', 'CAT01', 'P1', true, 'str', '招标项目编号', 'P1'),
('PROJECT_TYPE', '项目类型', 'CAT01', 'P0', true, 'str', '项目类型如：工程建设、政府采购、物资采购', 'P0'),
('PROJECT_CATEGORY', '项目分类', 'CAT01', 'P1', true, 'str', '具体行业分类', 'P1'),
('PROJECT_LOCATION', '项目地点', 'CAT01', 'P1', true, 'str', '项目实施地点', 'P1'),
('PROJECT_BUDGET', '预算金额', 'CAT01', 'P0', true, 'float', '项目预算金额（万元）', 'P0'),
('PROJECT_FUND_SOURCE', '资金来源', 'CAT01', 'P1', true, 'str', '项目资金来源', 'P1'),
-- CAT02
('TENDERER_NAME', '招标人名称', 'CAT02', 'P0', true, 'str', '招标人全称', 'P0'),
('TENDERER_CONTACT', '招标人联系方式', 'CAT02', 'P1', true, 'str', '招标人联系人及电话', 'P1'),
('TENDERER_ADDRESS', '招标人地址', 'CAT02', 'P2', true, 'str', '招标人联系地址', 'P2'),
-- CAT03
('PAYMENT_METHOD', '付款方式', 'CAT03', 'P1', true, 'str', '款项支付方式、付款条件', 'P1'),
('PAYMENT_ADVANCE', '预付款比例', 'CAT03', 'P2', true, 'float', '预付款占总金额比例', 'P2'),
-- CAT04
('REGISTER_DEADLINE', '报名截止时间', 'CAT04', 'P1', true, 'datetime', '投标报名截止时间', 'P1'),
('QUESTION_DEADLINE', '答疑截止时间', 'CAT04', 'P2', true, 'datetime', '疑问提交截止时间', 'P2'),
('BID_DEADLINE', '投标截止时间', 'CAT04', 'P0', true, 'datetime', '投标文件提交截止时间', 'P0'),
('OPEN_BID_TIME', '开标时间', 'CAT04', 'P1', true, 'datetime', '公开开标时间', 'P1'),
('BID_VALIDITY', '投标有效期', 'CAT04', 'P1', true, 'int', '投标有效期天数', 'P1'),
-- CAT05
('QUAL_QUALIFICATION', '企业资质', 'CAT05', 'P0', true, 'list', '投标企业应具备的资质证书', 'P0'),
('QUAL_ISO_CERT', 'ISO认证', 'CAT05', 'P1', true, 'list', 'ISO质量管理体系认证要求', 'P1'),
('QUAL_PERSONNEL', '人员要求', 'CAT05', 'P0', true, 'list', '项目经理及主要技术人员要求', 'P0'),
('QUAL_REGISTRATION', '注册建造师', 'CAT05', 'P1', true, 'list', '注册建造师专业及等级要求', 'P1'),
('QUAL_SAFETY_CERT', '安全生产许可证', 'CAT05', 'P1', true, 'str', '安全生产许可证要求', 'P1'),
-- CAT06
('QUAL_SIMILAR_EXPERIENCE', '类似项目业绩', 'CAT06', 'P0', true, 'dict', '类似项目业绩要求', 'P0'),
('QUAL_PERFORMANCE_AMOUNT', '业绩最低金额', 'CAT06', 'P1', true, 'float', '业绩合同金额下限（万元）', 'P1'),
('QUAL_PERFORMANCE_COUNT', '业绩数量要求', 'CAT06', 'P1', true, 'int', '类似项目数量要求', 'P1'),
-- CAT07
('TECH_REQUIREMENT', '技术参数要求', 'CAT07', 'P1', true, 'str', '主要技术参数、规格要求', 'P1'),
('TECH_STANDARD', '执行标准', 'CAT07', 'P2', true, 'str', '执行的国家或行业标准', 'P2'),
-- CAT08
('BID_BOND', '投标保证金', 'CAT08', 'P1', true, 'float', '投标保证金金额（万元）', 'P1'),
('PERFORMANCE_BOND', '履约保证金', 'CAT08', 'P1', true, 'float', '履约保证金比例', 'P1'),
('QUALITY_BOND', '质量保证金', 'CAT08', 'P2', true, 'float', '质量保证金比例', 'P2'),
-- CAT09
('EVAL_METHOD', '评标办法', 'CAT09', 'P1', true, 'str', '综合评分法/最低价法/经评审的最低价法', 'P1'),
('PRICE_SCORE_RATIO', '价格分占比', 'CAT09', 'P1', true, 'float', '价格分在总分中的占比', 'P1'),
('TECH_SCORE_RATIO', '技术分占比', 'CAT09', 'P1', true, 'float', '技术分在总分中的占比', 'P1'),
-- CAT10
('RISK_EXCLUSIVE', '排他性条款', 'CAT10', 'P1', true, 'str', '指定品牌、独家供应商等排他性条款', 'P1'),
('RISK_UNFAIR', '显失公平条款', 'CAT10', 'P1', true, 'str', '明显不公平的合同条款', 'P1'),
('RISK_PRICE_FIXED', '固定价格风险', 'CAT10', 'P2', true, 'str', '固定价格、不予调价条款', 'P2'),
('RISK_LIABILITY_CAP', '责任上限缺失', 'CAT10', 'P2', true, 'str', '承包商责任无限或缺失上限条款', 'P2'),
-- CAT11
('REJECT_QUAL_MISSING', '资质缺失否决', 'CAT11', 'P0', true, 'str', '资质证书缺失导致直接废标', 'P0'),
('REJECT_BOND_MISSING', '保证金缺失否决', 'CAT11', 'P0', true, 'str', '未提交投标保证金直接废标', 'P0'),
('REJECT_DEADLINE_MISS', '逾期递交否决', 'CAT11', 'P0', true, 'str', '投标文件逾期递交直接废标', 'P0'),
('REJECT_SIGNATURE_MISSING', '签章缺失否决', 'CAT11', 'P0', true, 'str', '法定代表人未签字或盖章废标', 'P0'),
('REJECT_BID_FORMAT', '投标格式不符', 'CAT11', 'P0', true, 'str', '投标文件格式不符合要求废标', 'P0');

INSERT INTO app.bid_tag_relation (source_tag_code, target_tag_code, relation_type, priority, is_active) VALUES
('PROJECT_NAME', 'PROJECT_CODE', 'similar', 'P1', true),
('QUAL_QUALIFICATION', 'QUAL_ISO_CERT', 'requires', 'P1', true),
('QUAL_QUALIFICATION', 'QUAL_SAFETY_CERT', 'requires', 'P1', true),
('QUAL_QUALIFICATION', 'QUAL_PERSONNEL', 'requires', 'P1', true),
('QUAL_PERSONNEL', 'QUAL_REGISTRATION', 'requires', 'P1', true),
('QUAL_SIMILAR_EXPERIENCE', 'QUAL_PERFORMANCE_AMOUNT', 'quantifies', 'P1', true),
('QUAL_SIMILAR_EXPERIENCE', 'QUAL_PERFORMANCE_COUNT', 'quantifies', 'P1', true),
('BID_BOND', 'PERFORMANCE_BOND', 'related', 'P2', true),
('EVAL_METHOD', 'PRICE_SCORE_RATIO', 'composes', 'P2', true),
('EVAL_METHOD', 'TECH_SCORE_RATIO', 'composes', 'P2', true),
('RISK_EXCLUSIVE', 'RISK_UNFAIR', 'implies', 'P2', true),
('REJECT_QUAL_MISSING', 'QUAL_QUALIFICATION', 'validates', 'P1', true),
('REJECT_BOND_MISSING', 'BID_BOND', 'validates', 'P1', true);

-- 视图
CREATE OR REPLACE VIEW app.v_tag_summary AS
SELECT
    c.category_code, c.category_name,
    COUNT(d.tag_id) AS tag_count,
    COUNT(DISTINCT d.level_code) AS level_count,
    COUNT(DISTINCT CASE WHEN d.priority = 'P0' THEN d.tag_code END) AS p0_count
FROM app.bid_tag_category c
LEFT JOIN app.bid_tag_dict d ON d.category_code = c.category_code AND d.is_active = true
GROUP BY c.category_code, c.category_name
ORDER BY c.sort_order;

COMMIT;
