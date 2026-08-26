// 用户与认证
export interface User {
  id: string
  username: string
  display_name: string
  roles: string[]
  created_at?: string
}

export type UserRole = 'system_admin' | 'project_owner' | 'bid_specialist' | 'legal_compliance' | 'material_manager' | 'readonly'

// 企业
export interface Enterprise {
  id: string
  name: string
  credit_code: string | null
  enterprise_type: string | null
  status: string
  created_at: string
  created_by: string
}

export interface EnterpriseMember {
  id: string
  enterprise_id: string
  user_id: string
  username: string | null
  display_name: string | null
  role_code: string
  status: string
  created_at: string
}

export interface EnterpriseWithMembers extends Enterprise {
  members: EnterpriseMember[]
}

// 项目
export interface Project {
  id: string
  name: string
  code: string
  purchaser: string
  project_type: string
  region: string
  bid_deadline: string
  status: ProjectStatus
  owner_id: string
  enterprise_ids: string[]
}

export type ProjectStatus = 'DRAFT' | 'ACTIVE' | 'ARCHIVED'

export interface ProjectMember {
  project_id: string
  user_id: string
  role: ProjectMemberRole
  user?: User
}

export type ProjectMemberRole = 'owner' | 'member'

// 文档
export interface Document {
  id: string
  project_id: string | null
  document_type: DocumentType
  logical_name: string
  current_version_id: string | null
  versions: DocumentVersion[]
}

// 项目文档列表接口返回的是轻量卡片，不包含完整版本信息。
export interface BidDocumentCard {
  doc_id: string
  doc_name: string
  parse_status: string
  created_at: string | null
}

export type DocumentType = 'TENDER' | 'ENTERPRISE' | 'LEGAL' | 'CASE'

export interface DocumentVersion {
  id: string
  version_no: number
  file_name: string
  file_size: number
  mime_type: string
  sha256: string
  parse_status: ParseStatus
  error_code: string | null
  error_message: string | null
  cleaning_summary: Record<string, any> | null
  created_at: string
  completed_at: string | null
}

export type ParseStatus = 'UPLOADED' | 'QUEUED' | 'PARSING' | 'PARSED' | 'STRUCTURING' | 'INDEXING' | 'READY' | 'FAILED'

export interface DocumentNode {
  id: string
  document_version_id: string
  node_type: string
  page_number: number | null
  section_path: string | null
  order_no: number
  content: string
  content_hash: string
  cleaned_content: string | null
  cleaning_metadata: Record<string, any>
  bbox: Record<string, any> | null
  metadata: Record<string, any>
}

export type NodeType = 'SECTION' | 'PARAGRAPH' | 'LIST' | 'TABLE' | 'CELL' | 'IMAGE'

export interface BBox {
  x: number
  y: number
  width: number
  height: number
}

// Evidence
export interface Evidence {
  id: string
  source_type: EvidenceSourceType
  document_version_id: string
  document_node_id: string
  page_number: number
  quoted_text: string
  content_hash: string
  confidence: number
  created_at: string
}

export type EvidenceSourceType = 'DOCUMENT_SECTION' | 'DOCUMENT_TEXT' | 'DOCUMENT_TABLE' | 'DOCUMENT_IMAGE' | 'USER_CONFIRMATION'

// Requirement
export interface Requirement {
  id: string
  project_id: string
  category: string
  title: string
  description: string | null
  conditions: Record<string, any>
  is_mandatory: boolean
  score: number | null
  confidence: number | null
  review_status: ReviewStatus
  primary_evidence_id: string | null
  evidence_ids: string[]
  reviewed_at: string | null
  review_note: string | null
}

export type RequirementCategory = 'PROJECT' | 'QUALIFICATION' | 'COMMERCIAL' | 'SCORING'
export type ReviewStatus = 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'DEFERRED'

// 风险
export interface Risk {
  id: string
  project_id: string
  rule_version_id: string | null
  risk_type: RiskType
  severity: RiskSeverity
  title: string
  description: string
  trigger_data: Record<string, any>
  confidence: number | null
  status: RiskStatus
  resolution: string | null
  primary_evidence_id: string | null
  evidence_ids: string[]
  created_at: string
  updated_at: string
}

export type RiskType = 'QUALIFICATION' | 'COMPLIANCE' | 'FORMAT' | 'TIME' | 'FINANCIAL' | 'TECHNICAL' | 'COMMERCIAL' | 'DOCUMENT'
export type RiskSeverity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO'
export type RiskStatus = 'PENDING' | 'CONFIRMED' | 'RESOLVED' | 'FALSE_POSITIVE' | 'IGNORED'

// 企业材料
export interface EnterpriseMaterial {
  id: string
  enterprise_id: string | null
  material_type: MaterialType
  name: string
  material_no: string | null
  issuer: string | null
  level: string | null
  valid_from: string | null
  valid_to: string | null
  amount: number | null
  currency: string
  attributes: Record<string, any>
  status: 'PENDING' | 'CONFIRMED' | 'REJECTED' | 'DEFERRED'
  evidence_ids: string[]
  documents: MaterialDocument[]
  created_at: string
  updated_at: string
}

export interface MaterialDocument {
  document_id: string
  document_version_id: string
  file_name: string
  version_no: number
  parse_status: string
}

export type MaterialType = 'CERTIFICATE' | 'QUALIFICATION' | 'PROJECT_EXPERIENCE' | 'PERSONNEL'

// 匹配结果
export interface MatchResult {
  id: string
  project_id: string
  requirement_id: string
  material_id: string | null
  automatic_status: MatchStatus
  final_status: MatchStatus
  reason: string
  missing_conditions: Record<string, any>[]
  is_overridden: boolean
  evidence_ids: string[]
  created_at: string
  updated_at: string
}

export type MatchStatus = 'MATCHED' | 'PARTIAL' | 'MISSING' | 'EXPIRED' | 'UNKNOWN' | 'CONFLICT'

// 决策
export interface Decision {
  id: string
  project_id: string
  suggestion: DecisionSuggestion
  hard_constraint_result: Record<string, any>
  reason: string
  missing_materials: Record<string, any>[]
  evidence_ids: string[]
  created_at: string
  created_by: string
}

export type DecisionSuggestion = 'RECOMMEND' | 'CAUTION' | 'HOLD' | 'REJECT'

// 报告（与后端 ReportResponse 对齐）
export interface Report {
  id: string
  project_id: string
  version_no: number
  status: ReportStatus
  error_code: string | null
  error_message: string | null
  generated_by: string
  generated_at: string | null
  created_at: string
  sections?: ReportSection[]
}

export interface ReportSection {
  section_code: string
  order_no: number
  content_markdown: string
  evidence_ids: string[]
}

export type ReportStatus = 'PENDING' | 'GENERATING' | 'READY' | 'FAILED'

// 任务
export interface Task {
  id: string
  task_type: TaskType
  target_type: string
  target_id: string
  status: TaskStatus
  attempt: number
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export type TaskType = 'PARSE_DOCUMENT' | 'EXTRACT_REQUIREMENTS' | 'INDEX_DOCUMENT' | 'RUN_RISK_CHECK' | 'RUN_MATCH' | 'GENERATE_DECISION' | 'GENERATE_REPORT' | 'RUN_PROJECT_ANALYSIS' | 'ANSWER_QUESTION'
export type TaskStatus = 'QUEUED' | 'RUNNING' | 'WAITING_HUMAN_REVIEW' | 'SUCCEEDED' | 'FAILED'

export interface AnalysisSnapshot {
  tender_version_ids: string[]
  enterprise_material_ids: string[]
  rule_version_ids: string[]
  stage_outputs: Record<string, Record<string, any>>
}

export interface AnalysisRun {
  id: string
  project_id: string
  status: 'QUEUED' | 'RUNNING' | 'WAITING_HUMAN' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
  current_stage: string
  task_id: string | null
  report_id: string | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
  created_at: string
  snapshot?: AnalysisSnapshot | null
}

// 规则
export interface Rule {
  id: string
  code: string
  version: number
  name: string
  risk_type: RiskType
  severity: RiskSeverity
  definition: Record<string, any>
  status: 'active' | 'inactive'
  created_at: string
}

// 审计日志
export interface AuditLog {
  id: string
  actor_id: string
  action: string
  target_type: string
  target_id: string
  before_summary: string | null
  after_summary: string | null
  created_at: string
  actor?: User
}

// Agent Run（与后端 AgentRunResponse 对齐）
export type AgentWorkflow = 'BID_READINESS_REVIEW' | 'COMPLIANCE_REVIEW' | 'MARKET_REVIEW'
export type AgentRunStatus = 'QUEUED' | 'RUNNING' | 'WAITING_HUMAN' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface AgentRunStep {
  step_name: string
  status: string
  attempt: number
  output_summary: Record<string, any>
  model_id: string | null
  input_hash: string | null
  output_hash: string | null
  latency_ms: number | null
  error_code: string | null
  error_message: string | null
  started_at: string | null
  completed_at: string | null
}

export interface AgentFinding {
  title: string
  severity: RiskSeverity
  conclusion: string
  recommended_action: string
  evidence_ids: string[]
  limitations: string[]
}

export interface SpecialistAssessment {
  agent: string
  summary: string
  findings: AgentFinding[]
  open_questions: string[]
  confidence: number
}

export interface StrategyAction {
  priority: 'P0' | 'P1' | 'P2'
  action: string
  owner_role: string
  evidence_ids: string[]
}

export interface StrategyRecommendation {
  bid_recommendation: 'PROCEED' | 'PROCEED_WITH_CONDITIONS' | 'HOLD'
  rationale: string
  priority_actions: StrategyAction[]
  residual_risks: string[]
  confidence: number
}

export interface EvidenceCritique {
  requires_human_review: boolean
  blockers: string[]
  unsupported_claims: string[]
  reviewer_focus: string[]
  conclusion: string
}

export interface AgentRecommendation {
  id: string
  kind: string
  source_agent: string
  title: string
  description: string
  risk_type: string | null
  severity: string | null
  priority: string | null
  owner_role: string | null
  status: string
  evidence_ids: string[]
  adopted_target_type: string | null
  adopted_target_id: string | null
  review_note: string | null
  reviewed_by: string | null
  reviewed_at: string | null
}

export interface AgentRun {
  id: string
  project_id: string
  source_document_version_id: string | null
  workflow: AgentWorkflow
  status: AgentRunStatus
  goal: string
  input_hash: string
  thread_id: string | null
  checkpoint_version: number
  requires_human_review: boolean
  result: Record<string, any>
  error_code: string | null
  error_message: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  evidence_ids: string[]
  steps: AgentRunStep[]
  recommendations: AgentRecommendation[]
}

// API 响应
export interface ApiResponse<T> {
  data: T
  message?: string
}

export interface PaginatedResponse<T> {
  data: T[]
  total: number
  page: number
  page_size: number
}

export interface ApiError {
  code: string
  message: string
  request_id: string
}

// 登录
export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  access_token: string
  token_type: string
  expires_in: number
  user: User
}

// 聊天消息
export type ChatMessageStatus = 'ok' | 'error' | 'streaming'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  streaming?: boolean
  status?: ChatMessageStatus
  created_at: string
}

export interface Citation {
  evidence_id: string
  document_id?: string
  content?: string
}

// 知识库
export interface KnowledgeEntry {
  entry_id: string
  version_id: string
  version_no: number
  knowledge_type: 'LEGAL' | 'CASE'
  title: string
  authority: string | null
  source_reference: string
  status: 'DRAFT' | 'PUBLISHED' | 'ARCHIVED'
  content: string
  issued_on: string | null
  effective_on: string | null
  citation_note: string | null
  published_at: string | null
  source_document_version_id: string | null
  source_parse_status: string | null
  source_cleaning_summary: Record<string, any> | null
}
