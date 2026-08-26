"""bid_tag_seed: 150 个标签 + 关系 + 优先级 + 分类

Revision: 202608310002
Down_revision: 202608310001
"""
import psycopg

revision = '202608310002'
down_revision = '202608310001'


def upgrade():
    from app.core.config import get_settings
    from urllib.parse import urlparse, parse_qs
    settings = get_settings()
    # 解析 postgresql+psycopg://... 为 psycopg 可用的参数字典
    url = settings.database_url.replace('postgresql+psycopg://', 'http://')
    parsed = urlparse(url)
    conn_params = {
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'dbname': parsed.path.lstrip('/') or 'postgres',
        'user': parsed.username or 'postgres',
        'password': parsed.password or '',
    }
    conn = psycopg.connect(**conn_params, autocommit=True)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO bid_tag_category (category_code, category_name, category_desc, sort_order) VALUES
        ('CAT01', '项目基本信息', '项目名称、编号、预算、地点、方式等', 1),
        ('CAT02', '招标人/代理机构', '招标人、采购人、代理机构联系信息', 2),
        ('CAT03', '投标人资格要求', '资质、业绩、财务、信誉资格条件', 3),
        ('CAT04', '时间节点', '报名、投标、开标、评标、公示等', 4),
        ('CAT05', '商务条款', '保证金、付款、报价、工期、质保等', 5),
        ('CAT06', '技术要求', '技术参数、性能、质量、验收、售后', 6),
        ('CAT07', '评标办法', '评标方法、价格分、技术分、否决项', 7),
        ('CAT08', '投标文件要求', '格式、签章、份数、电子标、加密', 8),
        ('CAT09', '合同条款', '合同类型、违约、争议、变更、终止', 9),
        ('CAT10', '风险条款', '罚款、限制性、排他、保密、知识产权', 10),
        ('CAT11', '否决性条款', '导致投标无效的强制性条款', 11)
        ON CONFLICT (category_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_level (level_code, level_name, level_desc, sort_order) VALUES
        ('P0', '关键必填', '必须100%提取', 1),
        ('P1', '重要推荐', '强烈建议提取', 2),
        ('P2', '一般可选', '辅助分析参考', 3)
        ON CONFLICT (level_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('PROJECT_NAME', '项目名称', 'CAT01', 'P0', 'string', true, false, '在招标公告或封面查找"项目名称""工程名称""采购项目名称"，提取完整正式名称', 'XX市轨道交通3号线一期工程'),
        ('PROJECT_NUMBER', '项目编号', 'CAT01', 'P0', 'string', true, false, '查找"项目编号""招标编号""项目代码""备案编号"', 'XYZ-2024-001'),
        ('TENDER_NUMBER', '招标编号', 'CAT01', 'P0', 'string', true, false, '查找"招标编号""招标登记号""招标批次号"', 'ZB-2024-0501'),
        ('PROJECT_LOCATION', '项目地点', 'CAT01', 'P0', 'string', true, false, '查找"项目地点""工程地点""交货地点""实施地点"', '北京市海淀区中关村大街1号'),
        ('PROJECT_BUDGET', '项目预算金额', 'CAT01', 'P0', 'number', true, false, '查找"预算金额""项目总投资""最高限价""控制价""采购预算"，需同时记录币种', '58000000.00 元'),
        ('PROJECT_CURRENCY', '币种', 'CAT01', 'P0', 'string', false, false, '提取货币单位：人民币/美元/欧元等', '人民币'),
        ('FUND_SOURCE', '资金来源', 'CAT01', 'P0', 'string', true, false, '查找"资金来源""资金落实情况"：财政资金/自筹资金/银行贷款/国债资金/世行贷款', '中央财政资金70%+地方自筹30%'),
        ('TENDER_METHOD', '招标方式', 'CAT01', 'P0', 'string', true, false, '查找"招标方式""采购方式"：公开招标/邀请招标/竞争性谈判/竞争性磋商/单一来源/询价', '公开招标'),
        ('TENDER_ORGANIZATION', '招标组织形式', 'CAT01', 'P1', 'string', false, false, '查找"组织形式"：自行招标/委托招标', '委托招标'),
        ('PROJECT_DURATION', '项目工期/交货期', 'CAT01', 'P0', 'string', true, false, '查找"工期""交货期""服务期""实施周期"', '工期：540日历天'),
        ('PROJECT_SCALE', '项目规模', 'CAT01', 'P1', 'string', false, false, '查找"项目规模""建设规模""采购数量"', '建筑面积58000平方米'),
        ('PROJECT_CATEGORY', '项目分类', 'CAT01', 'P1', 'string', false, false, '根据内容判断项目类型：工程/货物/服务；细分：施工/设计/监理/咨询/设备采购', '工程施工')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('TENDERER_NAME', '招标人名称', 'CAT02', 'P0', 'string', true, false, '查找"招标人""采购人""招标单位""项目业主"', 'XX市轨道交通集团有限公司'),
        ('TENDERER_ADDRESS', '招标人地址', 'CAT02', 'P1', 'string', false, false, '查找招标人通讯地址', '北京市西城区XX路XX号'),
        ('TENDERER_CONTACT', '招标人联系人', 'CAT02', 'P0', 'string', false, false, '查找"联系人""项目联系人"', '张三'),
        ('TENDERER_PHONE', '招标人联系电话', 'CAT02', 'P0', 'string', false, false, '查找"联系电话""电话""手机"', '010-12345678'),
        ('TENDERER_ID', '招标人统一社会信用代码', 'CAT02', 'P1', 'string', false, false, '查找"统一社会信用代码""组织机构代码"', '91110000XXXXXXXXXX'),
        ('AGENCY_NAME', '代理机构名称', 'CAT02', 'P0', 'string', false, false, '查找"招标代理机构""采购代理机构""受招标人委托"', 'XX招标有限公司'),
        ('AGENCY_ADDRESS', '代理机构地址', 'CAT02', 'P2', 'string', false, false, '查找代理机构通讯地址', '北京市朝阳区XX路XX号'),
        ('AGENCY_CONTACT', '代理机构联系人', 'CAT02', 'P0', 'string', false, false, '查找代理机构联系人', '李四'),
        ('AGENCY_PHONE', '代理机构联系电话', 'CAT02', 'P0', 'string', false, false, '查找代理机构电话', '010-87654321'),
        ('AGENCY_ID', '代理机构社会信用代码', 'CAT02', 'P2', 'string', false, false, '查找代理机构统一社会信用代码', '91110000XXXXXXXXXX')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('QUAL_BUSINESS_LICENSE', '营业执照要求', 'CAT03', 'P0', 'boolean', true, false, '查找"营业执照""独立法人资格""法人身份"等要求', '需提供有效的营业执照'),
        ('QUAL_REGISTERED_CAPITAL', '注册资本要求', 'CAT03', 'P0', 'string', false, false, '查找"注册资本""注册资金"', '注册资本不低于5000万元人民币'),
        ('QUAL_QUALIFICATION', '资质等级要求', 'CAT03', 'P0', 'array', true, true, '查找"资质等级""施工资质""设计资质""安全生产许可证"', '["建筑工程施工总承包一级","市政公用工程施工总承包一级"]'),
        ('QUAL_SIMILAR_EXPERIENCE', '类似业绩要求', 'CAT03', 'P0', 'json', true, true, '查找"类似业绩""业绩要求""项目业绩"', '{"min_count":3,"min_amount":"5000万","time_range":"近3年","type":"同类工程"}'),
        ('QUAL_FINANCIAL', '财务状况要求', 'CAT03', 'P0', 'json', false, false, '查找"财务状况""财务报告""审计报告""营业收入""资产负债率"', '{"report_years":3,"min_revenue":"1亿元","max_debt_ratio":"70%"}'),
        ('QUAL_CREDIT', '信誉要求', 'CAT03', 'P0', 'json', false, false, '查找"信誉""信用""失信""被列入"', '{"no_dishonest":true,"no_serious_illegal":true,"period":"近3年"}'),
        ('QUAL_JOINT_BID', '联合体投标', 'CAT03', 'P0', 'json', false, false, '查找"联合体""是否接受联合体"', '{"accepted":true,"max_members":2,"leader_required":true}'),
        ('QUAL_BLACKLIST', '黑名单限制', 'CAT03', 'P0', 'json', false, false, '查找"黑名单""禁止投标""限制投标""被列入"', '{"restricted_during":"2023-2025","authority":"住建部"}'),
        ('QUAL_TAX', '税务要求', 'CAT03', 'P1', 'string', false, false, '查找"完税证明""纳税信用等级""税务"', '提供近3年完税证明'),
        ('QUAL_SAFETY', '安全生产要求', 'CAT03', 'P1', 'string', false, false, '查找"安全生产许可证""安全生产考核合格证"', '持有有效的安全生产许可证'),
        ('QUAL_PERSONNEL', '关键人员要求', 'CAT03', 'P0', 'json', true, true, '查找"项目经理""项目负责人""技术负责人""施工员""专职安全员"', '[{"role":"项目经理","cert":"一级注册建造师","min_exp":"8年"}]'),
        ('QUAL_EQUIPMENT', '设备要求', 'CAT03', 'P1', 'json', false, true, '查找"自有设备""主要施工设备""检测设备"', '[{"name":"挖掘机","min_count":2}]'),
        ('QUAL_INSURANCE', '保险要求', 'CAT03', 'P1', 'string', false, false, '查找"工程保险""第三者责任险""雇主责任险"', '需投保工程一切险'),
        ('QUAL_FOREIGN', '外资准入要求', 'CAT03', 'P2', 'string', false, false, '查找"是否接受外资""境内注册"', '仅接受境内注册的法人'),
        ('QUAL_OTHER', '其他资格要求', 'CAT03', 'P2', 'text', false, true, '其他无法归类的特殊资格条件', '本省企业优先')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('TIME_SIGNUP_START', '报名开始时间', 'CAT04', 'P0', 'datetime', true, false, '查找"报名时间""发售时间""获取招标文件时间"的开始时间', '2024-05-01 09:00'),
        ('TIME_SIGNUP_END', '报名截止时间', 'CAT04', 'P0', 'datetime', true, false, '查找"报名截止""发售截止""获取招标文件截止"时间', '2024-05-10 17:00'),
        ('TIME_CLARIFY_END', '澄清截止时间', 'CAT04', 'P0', 'datetime', false, false, '查找"质疑截止""澄清要求截止""答疑截止"时间', '2024-05-12 17:00'),
        ('TIME_CLARIFY_REPLY', '澄清答复时间', 'CAT04', 'P1', 'datetime', false, false, '查找"答疑时间""澄清答复时间"', '2024-05-14 17:00'),
        ('TIME_BID_DEADLINE', '投标截止时间', 'CAT04', 'P0', 'datetime', true, false, '查找"投标截止""递交投标文件截止""开标时间"', '2024-05-20 09:00'),
        ('TIME_BID_OPEN', '开标时间', 'CAT04', 'P0', 'datetime', true, false, '查找"开标时间""开标日期"', '2024-05-20 09:00'),
        ('TIME_BID_OPEN_LOCATION', '开标地点', 'CAT04', 'P0', 'string', true, false, '查找"开标地点""开标地点地址"', 'XX市公共资源交易中心1号开标室'),
        ('TIME_EVALUATION', '评标时间', 'CAT04', 'P1', 'datetime', false, false, '查找"评标时间""评标日期"', '2024-05-21'),
        ('TIME_RESULT_PUBLIC', '中标公示时间', 'CAT04', 'P1', 'datetime', false, false, '查找"中标候选人公示""中标公示""中标结果公示"', '2024-05-25'),
        ('TIME_CONTRACT_SIGN', '合同签订期限', 'CAT04', 'P0', 'string', true, false, '查找"签订合同""合同签订"', '中标通知书发出后30日内')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('BID_BOND', '投标保证金', 'CAT05', 'P0', 'json', true, false, '查找"投标保证金""投标担保"', '{"amount":"100万元","ratio":"2","bank_guarantee":true,"return_days":30}'),
        ('PERFORMANCE_BOND', '履约保证金', 'CAT05', 'P0', 'json', true, false, '查找"履约保证金""履约担保"', '{"ratio":"10","max_amount":"500万","forms":["银行保函","现金"]}'),
        ('QUALITY_BOND', '质量保证金', 'CAT05', 'P1', 'json', false, false, '查找"质量保证金""质保金"', '{"ratio":"3","return_after":"质保期满"}'),
        ('PAYMENT_METHOD', '付款方式', 'CAT05', 'P0', 'json', true, false, '查找"付款方式""支付方式""结算方式"', '[{"stage":"预付款","ratio":"10"},{"stage":"进度款","ratio":"70"}]'),
        ('PAYMENT_CURRENCY', '付款币种', 'CAT05', 'P1', 'string', false, false, '查找"结算货币""支付币种"', '人民币'),
        ('PRICE_ADJUSTMENT', '价格调整条款', 'CAT05', 'P1', 'json', false, false, '查找"价格调整""调价""价格风险"', '{"allowed":true,"conditions":["材料价格波动±5%以上"]}'),
        ('PRICE_FORM', '报价方式', 'CAT05', 'P0', 'string', true, false, '查找"报价方式""报价形式"：总价/单价/费率/下浮率', '工程量清单报价'),
        ('PRICE_INCLUDE_TAX', '报价是否含税', 'CAT05', 'P0', 'string', true, false, '查找"含税""不含税""税金"', '含13%增值税'),
        ('MAX_PRICE', '最高限价', 'CAT05', 'P0', 'string', true, false, '查找"最高限价""控制价""最高投标限价""招标控制价"', '5800万元'),
        ('PROJECT_PERIOD_REQ', '工期/交货期要求', 'CAT05', 'P0', 'string', true, false, '查找"工期要求""交货期要求""服务期要求"', '不超过540日历天'),
        ('WARRANTY_PERIOD', '质保期要求', 'CAT05', 'P0', 'json', true, false, '查找"质保期""保修期""质量保证期"', '{"years":2,"start":"验收合格之日","scope":"全部工程"}'),
        ('DEFECT_PERIOD', '缺陷责任期', 'CAT05', 'P1', 'string', false, false, '查找"缺陷责任期"', '24个月'),
        ('DELAY_PENALTY', '逾期违约金', 'CAT05', 'P0', 'json', false, false, '查找"逾期违约""工期延误""延期违约金"', '{"per_day":"合同价0.1%","cap":"合同价10%"}'),
        ('QUALITY_PENALTY', '质量违约金', 'CAT05', 'P1', 'json', false, false, '查找"质量违约""质量不达标违约金"', '{"ratio":"合同价5%"}'),
        ('ADVANCE_PAYMENT_RATIO', '预付款比例', 'CAT05', 'P0', 'string', false, false, '查找"预付款""预付比例"', '合同价的10%'),
        ('PROGRESS_PAYMENT_RATIO', '进度款支付条件', 'CAT05', 'P0', 'json', false, false, '查找"进度款""进度支付""按月支付""按节点支付"', '[{"node":"地下室完成","ratio":"20%"}]'),
        ('SETTLEMENT_METHOD', '结算方式', 'CAT05', 'P0', 'string', false, false, '查找"结算""竣工结算""结算审核"', '竣工后按实际工程量结算'),
        ('INVOICE_REQUIREMENT', '发票要求', 'CAT05', 'P2', 'string', false, false, '查找"发票""增值税专用发票"', '提供增值税专用发票')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('TECH_PARAMS', '技术参数要求', 'CAT06', 'P0', 'json', true, true, '查找"技术参数""技术规格""技术要求"', '[{"name":"制冷量","value":">=500kW","star":true}]'),
        ('TECH_STANDARDS', '执行标准', 'CAT06', 'P0', 'array', true, true, '查找"执行标准""国家标准""行业标准""技术标准"', '["GB/T 50001","JGJ 3-2010"]'),
        ('TECH_PERFORMANCE', '性能指标', 'CAT06', 'P0', 'json', false, true, '查找"性能要求""性能指标""运行参数"', '[{"name":"能效比","value":">=4.0"}]'),
        ('TECH_QUALITY_STD', '质量标准', 'CAT06', 'P1', 'string', false, false, '查找"质量标准""质量等级""合格"', '达到国家施工质量验收合格标准'),
        ('TECH_ACCEPTANCE', '验收标准', 'CAT06', 'P0', 'json', false, false, '查找"验收""竣工验收""交付验收"', '{"standard":"一次性验收合格"}'),
        ('TECH_AFTERSALE', '售后服务要求', 'CAT06', 'P0', 'json', false, false, '查找"售后服务""服务响应""维修"', '{"response_time":"2小时","onsite_time":"24小时","free_years":3}'),
        ('TECH_TRAINING', '培训要求', 'CAT06', 'P1', 'json', false, false, '查找"培训""技术培训""操作培训"', '{"duration":"3天","attendees":"10人"}'),
        ('TECH_SPARE_PARTS', '备品备件要求', 'CAT06', 'P1', 'json', false, false, '查找"备品备件""备件清单"', '[{"name":"滤芯","min_qty":10}]'),
        ('TECH_MATERIAL', '材料设备要求', 'CAT06', 'P1', 'json', false, true, '查找"主要材料""设备品牌""材料品牌"', '[{"item":"管材","brands":["A","B","C"]}]'),
        ('TECH_INTELLECTUAL', '知识产权要求', 'CAT06', 'P1', 'string', false, false, '查找"知识产权""专利""技术秘密"', '投标产品无知识产权争议'),
        ('TECH_DRAWING', '图纸/方案要求', 'CAT06', 'P1', 'string', false, false, '查找"施工方案""技术方案""施工组织设计"', '需提供详细施工组织设计'),
        ('TECH_ENVIRONMENTAL', '环保要求', 'CAT06', 'P1', 'json', false, false, '查找"环保""绿色施工""节能""扬尘"', '{"green_construction":true,"noise_limit":"昼间70dB"}'),
        ('TECH_SAFETY', '安全要求', 'CAT06', 'P1', 'json', false, false, '查找"安全施工""文明施工""安全防护"', '{"zero_accident":true,"standard":"JGJ 59"}'),
        ('TECH_INNOVATION', '技术评分加分项', 'CAT06', 'P1', 'json', false, true, '查找"技术创新""加分""工法""专利"', '[{"item":"国家级工法","score":2}]'),
        ('TECH_OTHER', '其他技术要求', 'CAT06', 'P2', 'text', false, true, '其他特殊技术要求', '需提供BIM模型')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('EVAL_METHOD', '评标方法', 'CAT07', 'P0', 'string', true, false, '查找"评标办法""评标方法""评审方法"：综合评估法/最低评标价法/合理低价法', '综合评估法'),
        ('EVAL_TOTAL_SCORE', '总分', 'CAT07', 'P0', 'number', false, false, '查找"总分""满分"', '100'),
        ('EVAL_PRICE_WEIGHT', '价格分权重', 'CAT07', 'P0', 'number', true, false, '查找"价格分""商务分""报价分""价格评分"', '40'),
        ('EVAL_TECH_WEIGHT', '技术分权重', 'CAT07', 'P0', 'number', true, false, '查找"技术分""技术评分"', '40'),
        ('EVAL_BUSINESS_WEIGHT', '商务分权重', 'CAT07', 'P0', 'number', false, false, '查找"商务分""商务评分""信誉分"', '20'),
        ('EVAL_PRICE_BASE', '基准价计算方式', 'CAT07', 'P0', 'json', false, false, '查找"基准价""评标基准价""有效报价"', '{"method":"平均值"}'),
        ('EVAL_PRICE_DEVIATION', '报价偏差扣分', 'CAT07', 'P0', 'json', false, false, '查找"偏差""扣分""每偏离1%扣"', '{"per_1_percent_above":1,"cap":15}'),
        ('EVAL_LOW_PRICE_RULE', '低价处理规则', 'CAT07', 'P0', 'json', false, false, '查找"低于成本""异常低价""澄清"', '{"threshold":"基准价80%","action":"要求澄清"}'),
        ('EVAL_TECH_ITEMS', '技术评分项', 'CAT07', 'P0', 'json', true, true, '查找技术评分细则：施工方案、人员配备、设备配置、管理体系、进度计划', '[{"item":"施工方案","score":15}]'),
        ('EVAL_BUSINESS_ITEMS', '商务评分项', 'CAT07', 'P1', 'json', false, true, '查找商务评分项：业绩、财务、信誉、奖项', '[{"item":"类似业绩","score":10}]'),
        ('EVAL_AWARD_BONUS', '加分项', 'CAT07', 'P1', 'json', false, true, '查找"加分""奖项""工法""专利""ISO"', '[{"item":"鲁班奖","score":2,"max":4}]'),
        ('EVAL_LOCAL_PREF', '本地企业优惠', 'CAT07', 'P1', 'string', false, false, '查找"本地""本省""本企业"加分或优惠', '本省企业加1分'),
        ('EVAL_SMALL_ENTERPRISE', '中小企业优惠', 'CAT07', 'P1', 'json', false, false, '查找"中小企业""小微企业"加分或价格扣除', '{"price_deduct":"6%"}'),
        ('EVAL_COMMITTEE', '评标委员会构成', 'CAT07', 'P1', 'json', false, false, '查找"评标委员会""评标专家""成员"', '{"total":5,"expert":4,"tenderer":1}'),
        ('EVAL_RECOMMENDATION', '中标候选人推荐', 'CAT07', 'P1', 'string', false, false, '查找"推荐中标候选人""中标候选人数量"', '推荐3名中标候选人'),
        ('EVAL_REJECT_CLAUSES', '否决性条款汇总', 'CAT07', 'P0', 'json', true, true, '查找"否决""无效""废标""不予通过"', '[{"clause":"投标函未签字盖章","section":"3.1"}]')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('DOC_FORMAT', '文件格式要求', 'CAT08', 'P0', 'json', true, false, '查找"文件格式""投标文件格式""投标文件组成"', '{"original":1,"copy":3,"electronic":1}'),
        ('DOC_BINDING', '装订要求', 'CAT08', 'P1', 'string', false, false, '查找"装订""胶装""活页"', '胶装成册'),
        ('DOC_SEAL', '签章要求', 'CAT08', 'P0', 'json', true, false, '查找"盖章""签字""公章""法人签字""骑缝章"', '{"official_seal":true,"legal_rep_sign":true,"page_seal":true}'),
        ('DOC_COVER', '封面要求', 'CAT08', 'P1', 'string', false, false, '查找"封面""投标文件封面"', '按招标文件规定格式制作'),
        ('DOC_ENVELOPE', '密封要求', 'CAT08', 'P0', 'json', true, false, '查找"密封""信封""封套""包装"', '{"seal":"封套密封处加盖公章"}'),
        ('DOC_ELECTRONIC', '电子标要求', 'CAT08', 'P0', 'json', false, false, '查找"电子投标""电子招标""电子文件""光盘""U盘""加密"', '{"platform":"XX公共资源交易网","encrypt":"CA锁"}'),
        ('DOC_ENCRYPT', '加密要求', 'CAT08', 'P0', 'string', false, false, '查找"加密""密码""CA"', '使用CA数字证书加密'),
        ('DOC_PAGE_LIMIT', '页数限制', 'CAT08', 'P1', 'json', false, false, '查找"页数""字数"限制', '{"max_pages":200}'),
        ('DOC_LANGUAGE', '语言要求', 'CAT08', 'P1', 'string', false, false, '查找"语言""中文""外文"', '中文（外文需附中文翻译）'),
        ('DOC_OFFER_FORMAT', '报价表格式', 'CAT08', 'P0', 'string', true, false, '查找"报价表""投标报价表""已标价工程量清单"', '按招标文件附表格式'),
        ('DOC_BID_LETTER', '投标函要求', 'CAT08', 'P0', 'json', true, false, '查找"投标函""投标书""投标声明"', '{"required":true,"format":"附录1"}'),
        ('DOC_COMMITMENT', '承诺函要求', 'CAT08', 'P0', 'array', true, true, '查找"承诺函""声明""承诺书"', '["廉洁承诺书","质量承诺书"]'),
        ('DOC_POWER_OF_ATTORNEY', '授权委托书要求', 'CAT08', 'P0', 'string', false, false, '查找"授权委托书""法定代表人授权"', '需提供原件'),
        ('DOC_SUBMISSION_METHOD', '递交方式', 'CAT08', 'P0', 'string', true, false, '查找"递交""送达""上传"：现场/邮寄/线上', '线上递交+现场递交')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('CT_TYPE', '合同类型', 'CAT09', 'P0', 'string', true, false, '查找"合同类型""合同形式"：总价/单价/成本加酬金', '单价合同'),
        ('CT_TERM', '合同期限', 'CAT09', 'P0', 'string', false, false, '查找"合同期限""合同有效期"', '自签订之日起至缺陷责任期满'),
        ('CT_CHANGE', '变更条款', 'CAT09', 'P1', 'json', false, false, '查找"变更""工程变更""设计变更"', '{"max_ratio":"合同价10%"}'),
        ('CT_CLAIM', '索赔条款', 'CAT09', 'P1', 'json', false, false, '查找"索赔""工期索赔""费用索赔"', '{"notice_days":28}'),
        ('CT_TERMINATION', '合同终止', 'CAT09', 'P1', 'json', false, false, '查找"终止""解除""合同解除"', '{"conditions":["一方破产"]}'),
        ('CT_FORCE_MAJEURE', '不可抗力', 'CAT09', 'P1', 'json', false, false, '查找"不可抗力""force majeure"', '{"definition":"不能预见不能避免不能克服"}'),
        ('CT_DISPUTE', '争议解决', 'CAT09', 'P0', 'json', false, false, '查找"争议""纠纷""仲裁""诉讼"', '{"method":"诉讼","jurisdiction":"工程所在地法院"}'),
        ('CT_APPLICABLE_LAW', '适用法律', 'CAT09', 'P1', 'string', false, false, '查找"适用法律""法律适用"', '中华人民共和国法律'),
        ('CT_INTELLECTUAL', '知识产权归属', 'CAT09', 'P1', 'json', false, false, '查找"知识产权""专利""著作权"', '{"ownership":"双方共有"}'),
        ('CT_CONFIDENTIALITY', '保密条款', 'CAT09', 'P1', 'json', false, false, '查找"保密""保密义务""保密信息"', '{"period":"合同终止后5年"}'),
        ('CT_INSURANCE', '保险条款', 'CAT09', 'P1', 'json', false, false, '查找"保险""工程一切险""第三者责任险"', '{"type":"工程一切险","insured_by":"承包人"}'),
        ('CT_ASSIGNMENT', '转让条款', 'CAT09', 'P1', 'string', false, false, '查找"转让""合同转让"', '未经书面同意不得转让'),
        ('CT_SUBCONTRACT', '分包条款', 'CAT09', 'P0', 'json', false, false, '查找"分包""专业分包""主体工程不得分包"', '{"allowed":true,"main_work_forbidden":true,"max_ratio":"30%"}'),
        ('CT_SUPERVISION', '监理条款', 'CAT09', 'P1', 'string', false, false, '查找"监理""工程师""监理工程师"', '由招标人委托监理单位'),
        ('CT_RECEIVING', '接收条款', 'CAT09', 'P1', 'string', false, false, '查找"接收""交付""移交"', '通过竣工验收并交付使用'),
        ('CT_BREACH', '违约责任', 'CAT09', 'P0', 'json', false, false, '查找"违约""违约责任""违约金"', '{"delay":"0.1%/日","cap":"10%"}'),
        ('CT_EXTENSION', '合同补充条款', 'CAT09', 'P2', 'text', false, true, '查找"补充""其他约定""特别约定"', '双方另行约定'),
        ('CT_TAX', '税费条款', 'CAT09', 'P1', 'string', false, false, '查找"税费""税金""税收"', '由承包人承担所有税费')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('RISK_PENALTY', '罚款条款', 'CAT10', 'P0', 'json', true, true, '查找所有"罚款""罚金""扣款"条款', '[{"reason":"安全责任事故","amount":"10-50万"}]'),
        ('RISK_RESTRICTIVE', '限制性条款', 'CAT10', 'P0', 'array', true, true, '查找"必须""应当""不得""仅限"等限制性表述', '["不得使用XX品牌","必须雇佣本地工人≥30%"]'),
        ('RISK_EXCLUSIVE', '排他性条款', 'CAT10', 'P0', 'array', true, true, '查找指定品牌、指定供应商、指定分包商等排他性内容', '["指定设备品牌为ABB、施耐德"]'),
        ('RISK_INTELLECTUAL', '知识产权风险', 'CAT10', 'P1', 'json', false, true, '查找要求投标人放弃知识产权、技术秘密归属招标人的条款', '[{"clause":"投标方案知识产权归招标人"}]'),
        ('RISK_CONFIDENTIALITY', '保密风险', 'CAT10', 'P1', 'json', false, false, '查找过度保密义务、保密期过长、违约金过高的条款', '{"period":"永久","penalty":"合同价200%"}'),
        ('RISK_UNFAIR', '显失公平条款', 'CAT10', 'P0', 'array', true, true, '查找：风险全由投标人承担、无限责任、单方解约权等不公平条款', '["招标人可单方面调整工期且不予补偿"]'),
        ('RISK_PRICE_FIXED', '固定价格风险', 'CAT10', 'P0', 'string', false, false, '查找"固定价格""闭口合同""不予调价"等承担价格风险的条款', '合同期内价格不予调整'),
        ('RISK_LIABILITY_CAP', '责任上限', 'CAT10', 'P0', 'string', false, false, '查找"赔偿责任""责任上限""赔偿总额不超过"', '不超过合同总价的200%'),
        ('RISK_INSURANCE_BURDEN', '保险负担', 'CAT10', 'P1', 'json', false, false, '查找要求高额保险、特殊险种的条款', '{"type":"职业责任险","min_amount":"5000万"}'),
        ('RISK_LIQUID_DAMAGE', '损害赔偿范围', 'CAT10', 'P1', 'string', false, false, '查找"间接损失""利润损失""损害赔偿"是否包含间接损失', '包含直接和间接损失'),
        ('RISK_DISPUTE_COST', '争议成本', 'CAT10', 'P2', 'json', false, false, '查找"诉讼费用""仲裁费用""律师费用"承担方式', '{"loser_pays":true}'),
        ('RISK_FORCE_MAJEURE_DEF', '不可抗力定义范围', 'CAT10', 'P1', 'json', false, false, '检查不可抗力定义是否过窄或过宽', '{"includes":["疫情","战争","自然灾害"],"excludes":["政策变化"]}')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_dict (tag_code, tag_name, category_code, level_code, data_type, is_required, is_multi_value, extraction_prompt, value_example) VALUES
        ('REJECT_FORMAT', '格式否决项', 'CAT11', 'P0', 'array', true, true, '查找导致投标无效的格式问题：未盖章、未签字、未密封、未按规定格式', '["投标函未按格式填写","未加盖公章"]'),
        ('REJECT_QUALIFICATION', '资格否决项', 'CAT11', 'P0', 'array', true, true, '查找导致投标无效的资格问题：资质不符、无业绩、无人员证书', '["资质等级低于要求","项目经理无证书"]'),
        ('REJECT_PRICE', '报价否决项', 'CAT11', 'P0', 'array', true, true, '查找导致投标无效的报价问题：超过最高限价、低于成本、算术错误', '["报价超过最高限价"]'),
        ('REJECT_TIME', '时间否决项', 'CAT11', 'P0', 'array', true, true, '查找导致投标无效的时间问题：逾期递交、逾期澄清', '["逾期送达投标文件"]'),
        ('REJECT_CONTENT', '内容否决项', 'CAT11', 'P0', 'array', true, true, '查找导致投标无效的内容问题：不响应实质性条款、负偏离过半', '["对招标文件实质性条款负偏离"]'),
        ('REJECT_JOINT', '联合体否决项', 'CAT11', 'P0', 'array', false, true, '查找联合体投标相关否决条款', '["联合体成员变更","未提供联合体协议"]'),
        ('REJECT_BLACKLIST', '黑名单否决项', 'CAT11', 'P0', 'array', false, true, '查找列入失信、黑名单等导致否决的条款', '["被列入失信被执行人"]'),
        ('REJECT_CONFLICT', '利益冲突否决项', 'CAT11', 'P0', 'array', false, true, '查找存在利益冲突导致否决的条款', '["与招标人存在隶属关系"]'),
        ('REJECT_BOND', '保证金否决项', 'CAT11', 'P0', 'array', false, true, '查找保证金相关问题导致否决的条款', '["未按金额提交投标保证金"]'),
        ('REJECT_OTHER', '其他否决项', 'CAT11', 'P0', 'array', false, true, '其他导致投标无效的条款', '["投标文件字迹模糊无法辨认"]')
        ON CONFLICT (tag_code) DO NOTHING
    """)

    cur.execute("""
        INSERT INTO bid_tag_relation (source_tag_code, target_tag_code, relation_type, relation_desc, rule_json) VALUES
        ('BID_BOND', 'PROJECT_BUDGET', 'CONSTRAINS', '投标保证金不超过项目预算2%', '{"op":"<=","value":"PROJECT_BUDGET*0.02"}'),
        ('PERFORMANCE_BOND', 'PROJECT_BUDGET', 'CONSTRAINS', '履约保证金不超过合同价10%', '{"op":"<=","value":"CONTRACT_PRICE*0.10"}'),
        ('MAX_PRICE', 'PROJECT_BUDGET', 'CONSTRAINS', '最高限价不超过项目预算', '{"op":"<=","value":"PROJECT_BUDGET"}'),
        ('PROJECT_DURATION', 'PROJECT_PERIOD_REQ', 'CONSTRAINS', '投标工期不得超过要求工期', '{"op":"<="}'),
        ('QUAL_JOINT_BID', 'DOC_COMMITMENT', 'DEPENDS_ON', '联合体投标需提供联合体协议', NULL),
        ('TENDER_METHOD', 'EVAL_METHOD', 'DEPENDS_ON', '招标方式决定评标方法', NULL),
        ('QUAL_BLACKLIST', 'REJECT_BLACKLIST', 'TRIGGERS', '黑名单触发投标无效', NULL),
        ('MAX_PRICE', 'REJECT_PRICE', 'TRIGGERS', '超最高限价触发投标无效', NULL),
        ('DOC_SEAL', 'REJECT_FORMAT', 'TRIGGERS', '未盖章触发投标无效', NULL),
        ('QUAL_QUALIFICATION', 'REJECT_QUALIFICATION', 'TRIGGERS', '资质不符触发投标无效', NULL),
        ('TIME_SIGNUP_END', 'TIME_BID_DEADLINE', 'BEFORE', '报名截止先于投标截止', '{"order":"before"}'),
        ('TIME_BID_DEADLINE', 'TIME_BID_OPEN', 'EQUAL_OR_BEFORE', '开标时间不早于投标截止', '{"order":"equal_or_after"}'),
        ('TIME_RESULT_PUBLIC', 'TIME_CONTRACT_SIGN', 'BEFORE', '公示先于合同签订', '{"order":"before"}'),
        ('QUAL_BUSINESS_LICENSE', 'QUAL_QUALIFICATION', 'COMPOSES', '资格要求组合', NULL),
        ('QUAL_QUALIFICATION', 'QUAL_SIMILAR_EXPERIENCE', 'COMPOSES', '资格要求组合', NULL)
        ON CONFLICT DO NOTHING
    """)

    cur.close()
    conn.close()


def downgrade():
    import psycopg
    from urllib.parse import urlparse
    from app.core.config import get_settings
    settings = get_settings()
    url = settings.database_url.replace('postgresql+psycopg://', 'http://')
    parsed = urlparse(url)
    conn_params = {
        'host': parsed.hostname or 'localhost',
        'port': parsed.port or 5432,
        'dbname': parsed.path.lstrip('/') or 'postgres',
        'user': parsed.username or 'postgres',
        'password': parsed.password or '',
    }
    conn = psycopg.connect(**conn_params, autocommit=True)
    cur = conn.cursor()
    cur.execute("TRUNCATE bid_tag_relation, bid_tag_dict, bid_tag_level, bid_tag_category CASCADE")
    cur.close()
    conn.close()
