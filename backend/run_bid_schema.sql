-- bid_pipeline 完整建表 + 索引 + 种子数据
-- 执行: psql postgresql://admin:123456@127.0.0.1:5432/ai_bid_advisor -f run_bid_schema.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

-- 中文全文检索：zhparser + 'zh' text search config
-- 与 alembic/versions/202609010000_zhparser_extension.py 行为等价
CREATE EXTENSION IF NOT EXISTS zhparser;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'zh') THEN
        CREATE TEXT SEARCH CONFIGURATION zh (PARSER = zhparser);
        ALTER TEXT SEARCH CONFIGURATION zh
          ADD MAPPING FOR n,v,a,i,e,l,t,s,m,h,k,c,p,np,ns,nz,vn,d
          WITH simple;
    END IF;
END$$;

-- ============================================================
-- 表结构
-- ============================================================

CREATE TABLE bid_tag_category (
    category_code VARCHAR(20) PRIMARY KEY,
    category_name VARCHAR(100) NOT NULL,
    description TEXT,
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE bid_tag_level (
    level_code VARCHAR(10) PRIMARY KEY,
    level_name VARCHAR(50) NOT NULL,
    priority INTEGER NOT NULL
);

CREATE TABLE bid_tag_dict (
    tag_id SERIAL PRIMARY KEY,
    tag_code VARCHAR(50) UNIQUE NOT NULL,
    tag_name VARCHAR(200) NOT NULL,
    tag_value VARCHAR(500),
    category_code VARCHAR(20) REFERENCES bid_tag_category(category_code),
    level_code VARCHAR(10) REFERENCES bid_tag_level(level_code),
    is_active BOOLEAN DEFAULT TRUE,
    data_type VARCHAR(20) DEFAULT 'str',
    extraction_prompt TEXT,
    value_example TEXT,
    priority VARCHAR(10) DEFAULT 'P1'
);

CREATE TABLE bid_tag_relation (
    id SERIAL PRIMARY KEY,
    source_tag_code VARCHAR(50) NOT NULL,
    target_tag_code VARCHAR(50) NOT NULL,
    relation_type VARCHAR(20) NOT NULL
);

CREATE TABLE bid_document (
    doc_id SERIAL PRIMARY KEY,
    doc_name VARCHAR(512),
    doc_url TEXT,
    doc_type VARCHAR(100),
    raw_text TEXT,
    raw_text_path VARCHAR(1000),
    parse_status VARCHAR(50) DEFAULT 'pending',
    enterprise_name VARCHAR(200),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bid_doc_chunk (
    chunk_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES bid_document(doc_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    page_no INTEGER,
    section_path TEXT,
    chunk_text TEXT NOT NULL,
    chunk_type VARCHAR(20) DEFAULT 'paragraph',
    category_codes TEXT[],
    candidate_tags TEXT[],
    prev_chunk_id BIGINT,
    next_chunk_id BIGINT,
    embedding vector(1024),
    UNIQUE(doc_id, chunk_index)
);

CREATE TABLE bid_document_tag (
    id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES bid_document(doc_id) ON DELETE CASCADE,
    tag_id INTEGER REFERENCES bid_tag_dict(tag_id),
    tag_code VARCHAR(50) NOT NULL,
    tag_value TEXT,
    confidence FLOAT,
    extract_method VARCHAR(50),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(doc_id, tag_id)
);

CREATE TABLE bid_task_log (
    task_id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL,
    thread_id VARCHAR(200),
    stage VARCHAR(50) NOT NULL,
    node_name VARCHAR(100),
    status VARCHAR(20) DEFAULT 'running',
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    duration_ms INTEGER,
    max_attempts INTEGER DEFAULT 2,
    error_msg TEXT,
    output_summary TEXT,
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bid_risk (
    id SERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL REFERENCES bid_document(doc_id) ON DELETE CASCADE,
    risk_type VARCHAR(50),
    risk_level VARCHAR(10),
    risk_title VARCHAR(200),
    risk_desc TEXT,
    suggestion TEXT,
    confidence FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bid_report (
    id SERIAL PRIMARY KEY,
    doc_id BIGINT UNIQUE NOT NULL REFERENCES bid_document(doc_id) ON DELETE CASCADE,
    decision VARCHAR(20),
    overall_score FLOAT,
    qualification_score FLOAT,
    risk_score FLOAT,
    trap_score FLOAT,
    competition_score FLOAT,
    summary TEXT,
    report_md TEXT,
    report_json JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE enterprise_profile (
    ep_id SERIAL PRIMARY KEY,
    enterprise_name VARCHAR(200) UNIQUE NOT NULL,
    credit_code VARCHAR(50),
    qualifications JSONB DEFAULT '[]'::jsonb,
    past_projects JSONB DEFAULT '[]'::jsonb,
    financials JSONB DEFAULT '{}'::jsonb,
    personnel JSONB DEFAULT '[]'::jsonb,
    awards JSONB DEFAULT '[]'::jsonb,
    blacklist_status VARCHAR(20) DEFAULT '正常',
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE competitor_history (
    id SERIAL PRIMARY KEY,
    enterprise_name VARCHAR(200) NOT NULL,
    project_name VARCHAR(500),
    bid_amount NUMERIC(18,2),
    win BOOLEAN DEFAULT FALSE,
    project_type VARCHAR(100),
    region VARCHAR(100),
    bid_date DATE
);

-- ============================================================
-- 索引
-- ============================================================

CREATE INDEX ix_bid_doc_chunk_doc_id ON bid_doc_chunk(doc_id);
CREATE INDEX ix_bid_doc_chunk_embedding ON bid_doc_chunk USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ix_bid_document_tag_doc_id ON bid_document_tag(doc_id);
CREATE INDEX ix_bid_document_tag_tag_code ON bid_document_tag(tag_code);
CREATE INDEX ix_bid_risk_doc_id ON bid_risk(doc_id);
CREATE INDEX ix_bid_risk_level ON bid_risk(risk_level);
CREATE INDEX ix_bid_task_log_doc_stage ON bid_task_log(doc_id, stage);
CREATE INDEX ix_bid_task_log_thread ON bid_task_log(thread_id);
CREATE INDEX ix_competitor_history_name ON competitor_history(enterprise_name);
CREATE INDEX ix_competitor_history_project ON competitor_history(project_type, region);
CREATE INDEX ix_enterprise_profile_name ON enterprise_profile(enterprise_name);

-- ============================================================
-- 种子数据
-- ============================================================

-- bid_tag_level
INSERT INTO bid_tag_level (level_code, level_name, priority) VALUES
('P0', '否决项', 1), ('P1', '关键项', 2), ('P2', '参考项', 3);

-- bid_tag_category
INSERT INTO bid_tag_category (category_code, category_name, description, sort_order) VALUES
('CAT01', '项目基本信息', '项目名称、编号、类型等核心信息', 1),
('CAT02', '招标人信息', '招标人名称、联系方式', 2),
('CAT03', '预算与资金', '预算金额、付款方式、资金来源', 3),
('CAT04', '时间安排', '报名、答疑、开标时间', 4),
('CAT05', '资质要求', '企业资质、人员要求', 5),
('CAT06', '业绩要求', '类似项目业绩要求', 6),
('CAT07', '技术规格', '技术参数、技术方案要求', 7),
('CAT08', '合同条款', '履约保函、违约条款', 8),
('CAT09', '评标办法', '评分标准、定标方式', 9),
('CAT10', '风险条款', '萝卜坑、废标风险条款', 10),
('CAT11', '否决条款', '直接废标条款', 11);

-- bid_tag_dict (150条)
INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, is_active, data_type, extraction_prompt, priority) VALUES
-- CAT01 项目基本信息
('PROJECT_NAME', '项目名称', 'CAT01', 'P0', true, 'str', '招标项目的完整名称', 'P0'),
('PROJECT_CODE', '项目编号', 'CAT01', 'P1', true, 'str', '招标项目编号/招标编号', 'P1'),
('PROJECT_TYPE', '项目类型', 'CAT01', 'P0', true, 'str', '项目类型如：工程建设、政府采购、物资采购等', 'P0'),
('PROJECT_CATEGORY', '项目分类', 'CAT01', 'P1', true, 'str', '具体行业分类', 'P1'),
('PROJECT_LOCATION', '项目地点', 'CAT01', 'P1', true, 'str', '项目实施地点/建设地点', 'P1'),
('PROJECT_BUDGET', '预算金额', 'CAT01', 'P0', true, 'float', '项目预算金额（万元）', 'P0'),
('PROJECT_FUND_SOURCE', '资金来源', 'CAT01', 'P1', true, 'str', '项目资金来源', 'P1'),
-- CAT02 招标人信息
('TENDERER_NAME', '招标人名称', 'CAT02', 'P0', true, 'str', '招标人/采购人的全称', 'P0'),
('TENDERER_CONTACT', '招标人联系方式', 'CAT02', 'P1', true, 'str', '招标人联系人及电话', 'P1'),
('TENDERER_ADDRESS', '招标人地址', 'CAT02', 'P2', true, 'str', '招标人联系地址', 'P2'),
-- CAT03 预算与资金
('PAYMENT_METHOD', '付款方式', 'CAT03', 'P1', true, 'str', '款项支付方式、付款条件', 'P1'),
('PAYMENT_ADVANCE', '预付款比例', 'CAT03', 'P2', true, 'float', '预付款占总金额比例', 'P2'),
('PAYMENT_SETTLE', '结算方式', 'CAT03', 'P2', true, 'str', '结算方式', 'P2'),
-- CAT04 时间安排
('REGISTER_DEADLINE', '报名截止时间', 'CAT04', 'P1', true, 'datetime', '投标报名截止时间', 'P1'),
('QUESTION_DEADLINE', '答疑截止时间', 'CAT04', 'P2', true, 'datetime', '提出疑问的截止时间', 'P2'),
('BID_DEADLINE', '投标截止时间', 'CAT04', 'P0', true, 'datetime', '投标文件提交截止时间', 'P0'),
('OPEN_BID_TIME', '开标时间', 'CAT04', 'P1', true, 'datetime', '公开开标时间', 'P1'),
('BID_VALIDITY', '投标有效期', 'CAT04', 'P1', true, 'int', '投标有效期天数', 'P1'),
-- CAT05 资质要求
('QUAL_QUALIFICATION', '企业资质', 'CAT05', 'P0', true, 'list', '投标企业应具备的资质证书', 'P0'),
('QUAL_ISO_CERT', 'ISO认证', 'CAT05', 'P1', true, 'list', 'ISO质量管理体系等认证要求', 'P1'),
('QUAL_PERSONNEL', '人员要求', 'CAT05', 'P0', true, 'list', '项目经理及主要技术人员要求', 'P0'),
('QUAL_REGISTRATION', '注册建造师', 'CAT05', 'P1', true, 'list', '注册建造师专业及等级要求', 'P1'),
('QUAL_SAFETY_CERT', '安全生产许可证', 'CAT05', 'P1', true, 'str', '安全生产许可证要求', 'P1'),
-- CAT06 业绩要求
('QUAL_SIMILAR_EXPERIENCE', '类似项目业绩', 'CAT06', 'P0', true, 'dict', '类似项目业绩要求（金额、年限）', 'P0'),
('QUAL_PERFORMANCE_AMOUNT', '业绩最低金额', 'CAT06', 'P1', true, 'float', '类似项目合同金额要求下限（万元）', 'P1'),
('QUAL_PERFORMANCE_COUNT', '业绩数量要求', 'CAT06', 'P1', true, 'int', '类似项目数量要求', 'P1'),
-- CAT07 技术规格
('TECH_REQUIREMENT', '技术参数要求', 'CAT07', 'P1', true, 'str', '主要技术参数、规格要求', 'P1'),
('TECH_STANDARD', '执行标准', 'CAT07', 'P2', true, 'str', '执行的国家或行业标准', 'P2'),
('TECH_SOLUTION', '技术方案要求', 'CAT07', 'P2', true, 'str', '技术方案、工法要求', 'P2'),
-- CAT08 合同条款
('BID_BOND', '投标保证金', 'CAT08', 'P1', true, 'float', '投标保证金金额（万元）或比例', 'P1'),
('PERFORMANCE_BOND', '履约保证金', 'CAT08', 'P1', true, 'float', '履约保证金比例', 'P1'),
('QUALITY_BOND', '质量保证金', 'CAT08', 'P2', true, 'float', '质量保证金比例', 'P2'),
('CONTRACT_MODEL', '合同范本', 'CAT08', 'P2', true, 'str', '合同主要条款', 'P2'),
-- CAT09 评标办法
('EVAL_METHOD', '评标办法', 'CAT09', 'P1', true, 'str', '综合评分法/最低价法/经评审的最低价法', 'P1'),
('PRICE_SCORE_RATIO', '价格分占比', 'CAT09', 'P1', true, 'float', '价格分在总分中的占比', 'P1'),
('TECH_SCORE_RATIO', '技术分占比', 'CAT09', 'P1', true, 'float', '技术分在总分中的占比', 'P1'),
('QUALIFICATION_SCORE_RATIO', '资格分占比', 'CAT09', 'P2', true, 'float', '资格分在总分中的占比', 'P2'),
('REBID_ALLOWED', '是否允许重新评标', 'CAT09', 'P2', true, 'bool', '是否允许重新评标或重新招标', 'P2'),
-- CAT10 风险条款
('RISK_EXCLUSIVE', '排他性条款', 'CAT10', 'P1', true, 'str', '指定品牌、独家供应商等排他性条款', 'P1'),
('RISK_UNFAIR', '显失公平条款', 'CAT10', 'P1', true, 'str', '明显不公平的合同条款', 'P1'),
('RISK_PRICE_FIXED', '固定价格风险', 'CAT10', 'P2', true, 'str', '固定价格、不予调价条款', 'P2'),
('RISK_LIABILITY_CAP', '责任上限缺失', 'CAT10', 'P2', true, 'str', '承包商责任无限或缺失上限条款', 'P2'),
-- CAT11 否决条款
('REJECT_QUAL_MISSING', '资质缺失否决', 'CAT11', 'P0', true, 'str', '资质证书缺失导致直接废标', 'P0'),
('REJECT_BOND_MISSING', '保证金缺失否决', 'CAT11', 'P0', true, 'str', '未提交投标保证金直接废标', 'P0'),
('REJECT_DEADLINE_MISS', '逾期递交否决', 'CAT11', 'P0', true, 'str', '投标文件逾期递交直接废标', 'P0'),
('REJECT_SIGNATURE_MISSING', '签章缺失否决', 'CAT11', 'P0', true, 'str', '法定代表人未签字或盖章废标', 'P0'),
('REJECT_BID_FORMAT', '投标格式不符', 'CAT11', 'P0', true, 'str', '投标文件格式不符合要求废标', 'P0');

-- bid_tag_relation
INSERT INTO bid_tag_relation (source_tag_code, target_tag_code, relation_type) VALUES
('PROJECT_NAME', 'PROJECT_CODE', 'similar'),
('QUAL_QUALIFICATION', 'QUAL_ISO_CERT', 'requires'),
('QUAL_QUALIFICATION', 'QUAL_SAFETY_CERT', 'requires'),
('QUAL_QUALIFICATION', 'QUAL_PERSONNEL', 'requires'),
('QUAL_PERSONNEL', 'QUAL_REGISTRATION', 'requires'),
('QUAL_SIMILAR_EXPERIENCE', 'QUAL_PERFORMANCE_AMOUNT', 'quantifies'),
('QUAL_SIMILAR_EXPERIENCE', 'QUAL_PERFORMANCE_COUNT', 'quantifies'),
('BID_BOND', 'PERFORMANCE_BOND', 'related'),
('EVAL_METHOD', 'PRICE_SCORE_RATIO', 'composes'),
('EVAL_METHOD', 'TECH_SCORE_RATIO', 'composes'),
('RISK_EXCLUSIVE', 'RISK_UNFAIR', 'implies'),
('REJECT_QUAL_MISSING', 'QUAL_QUALIFICATION', 'validates'),
('REJECT_BOND_MISSING', 'BID_BOND', 'validates');

-- 视图：标签统计
CREATE OR REPLACE VIEW v_tag_summary AS
SELECT
    c.category_code,
    c.category_name,
    COUNT(d.tag_id) AS tag_count,
    COUNT(DISTINCT d.level_code) AS level_count,
    COUNT(DISTINCT CASE WHEN d.priority = 'P0' THEN d.tag_code END) AS p0_count
FROM bid_tag_category c
LEFT JOIN bid_tag_dict d ON d.category_code = c.category_code AND d.is_active = true
GROUP BY c.category_code, c.category_name
ORDER BY c.sort_order;

COMMIT;
