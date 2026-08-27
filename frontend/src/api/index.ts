import request from '@/utils/request'
import type {
  LoginRequest,
  LoginResponse,
  User,
  Project,
  ProjectMember,
  Document,
  BidDocumentCard,
  DocumentVersion,
  DocumentNode,
  Evidence,
  Requirement,
  Risk,
  EnterpriseMaterial,
  Enterprise,
  EnterpriseMember,
  EnterpriseWithMembers,
  MatchResult,
  Decision,
  Report,
  Task,
  Rule,
  AuditLog,
  AnalysisRun,
  AgentRun,
  AgentRecommendation,
  AgentWorkflow,
  PaginatedResponse,
  KnowledgeEntry,
} from '@/types'

export interface ReadinessStatus {
  status: 'ok' | string
  checks: Record<string, boolean>
  ai_available: boolean
}

// 健康检查不走 /api/v1，使用独立的只读就绪端点。
// 生产部署时由反向代理将该路径转发到 API。
export const systemApi = {
  getReadiness: async (): Promise<ReadinessStatus> => {
    const response = await fetch('/health/ready', { headers: { Accept: 'application/json' } })
    if (!response.ok) {
      throw new Error(`健康检查失败（HTTP ${response.status}）`)
    }
    return response.json() as Promise<ReadinessStatus>
  },
}

// 认证
export const authApi = {
  login: (data: LoginRequest) =>
    request.post<LoginResponse>('/auth/login', data) as unknown as Promise<LoginResponse>,

  logout: () =>
    request.post('/auth/logout'),

  getCurrentUser: () =>
    request.get<User>('/me') as unknown as Promise<User>,
}

// 企业
export const enterpriseApi = {
  list: () =>
    request.get<Enterprise[]>('/enterprises') as unknown as Promise<Enterprise[]>,

  get: (id: string) =>
    request.get<EnterpriseWithMembers>(`/enterprises/${id}`) as unknown as Promise<EnterpriseWithMembers>,

  create: (data: { name: string; credit_code?: string; enterprise_type?: string }) =>
    request.post<Enterprise>('/enterprises', data) as unknown as Promise<Enterprise>,

  update: (id: string, data: { name?: string; credit_code?: string; enterprise_type?: string; status?: string }) =>
    request.patch<Enterprise>(`/enterprises/${id}`, data) as unknown as Promise<Enterprise>,

  delete: (id: string) =>
    request.delete(`/enterprises/${id}`),

  getMembers: (id: string) =>
    request.get<EnterpriseMember[]>(`/enterprises/${id}/members`) as unknown as Promise<EnterpriseMember[]>,

  addMember: (id: string, data: { user_id: string; role_code: string }) =>
    request.post<EnterpriseMember>(`/enterprises/${id}/members`, data) as unknown as Promise<EnterpriseMember>,

  updateMember: (enterpriseId: string, memberId: string, data: { role_code?: string; status?: string }) =>
    request.patch<EnterpriseMember>(`/enterprises/${enterpriseId}/members/${memberId}`, data) as unknown as Promise<EnterpriseMember>,

  removeMember: (enterpriseId: string, memberId: string) =>
    request.delete(`/enterprises/${enterpriseId}/members/${memberId}`),
}

// 项目
export const projectApi = {
  list: (params?: { status?: string }) =>
    request.get<Project[]>('/projects', { params }) as unknown as Promise<Project[]>,

  get: (id: string) =>
    request.get<Project>(`/projects/${id}`) as unknown as Promise<Project>,

  create: (data: any) =>
    request.post<Project>('/projects', data) as unknown as Promise<Project>,

  update: (id: string, data: Partial<Project>) =>
    request.patch<Project>(`/projects/${id}`, data) as unknown as Promise<Project>,

  archive: (id: string) =>
    request.post(`/projects/${id}/archive`),

  delete: (id: string) =>
    request.delete(`/projects/${id}`),

  getMembers: (id: string) =>
    request.get<ProjectMember[]>(`/projects/${id}/members`) as unknown as Promise<ProjectMember[]>,

  addMember: (id: string, userId: string, role: string) =>
    request.post(`/projects/${id}/members`, { user_id: userId, role }),

}

export const analysisApi = {
  run: (projectId: string) =>
    request.post<Task>(`/projects/${projectId}/analysis-runs`) as unknown as Promise<Task>,

  list: (projectId: string) =>
    request.get<AnalysisRun[]>(`/projects/${projectId}/analysis-runs`) as unknown as Promise<AnalysisRun[]>,

  get: (runId: string) =>
    request.get<AnalysisRun>(`/analysis-runs/${runId}`) as unknown as Promise<AnalysisRun>,

}

// 文档
export interface DocumentTaskResult {
  document_id: string
  document_version_id: string
  version_no: number
  task: { id: string; status: string; task_type: string }
}

export const documentApi = {
  list: (projectId: string) =>
    request.get<BidDocumentCard[]>(`/projects/${projectId}/documents`) as unknown as Promise<BidDocumentCard[]>,

  // 上传 TENDER 招标文件：后端自动入队 bid_pipeline，返回版本与任务信息
  upload: (projectId: string, file: File, documentType: 'TENDER' | 'ENTERPRISE') => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('document_type', documentType)
    return request.post<DocumentTaskResult>(`/projects/${projectId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }) as unknown as Promise<DocumentTaskResult>
  },

  get: (id: string) =>
    request.get<Document>(`/documents/${id}`) as unknown as Promise<Document>,

  getVersions: (id: string) =>
    request.get<DocumentVersion[]>(`/documents/${id}/versions`) as unknown as Promise<DocumentVersion[]>,

  getNodes: (id: string, params?: { offset?: number; limit?: number; version_no?: number }) =>
    request.get<{ document_id: string; document_version_id: string; items: DocumentNode[]; offset: number; limit: number }>(`/documents/${id}/nodes`, { params }) as unknown as Promise<{ document_id: string; document_version_id: string; items: DocumentNode[]; offset: number; limit: number }>,

  retry: (id: string) =>
    request.post(`/documents/${id}/retry`),

  downloadUrl: (id: string) =>
    request.get<{ url: string }>(`/documents/${id}/download`) as unknown as Promise<{ url: string }>,
}

// Evidence
export const evidenceApi = {
  get: (id: string) =>
    request.get<Evidence>(`/evidences/${id}`) as unknown as Promise<Evidence>,
}

// Requirement
export const requirementApi = {
  list: (projectId: string) =>
    request.get<Requirement[]>(`/projects/${projectId}/requirements`) as unknown as Promise<Requirement[]>,

  review: (projectId: string, id: string, data: { review_status: string; review_note?: string }) =>
    request.patch<Requirement>(`/projects/${projectId}/requirements/${id}`, data) as unknown as Promise<Requirement>,

  bulkReview: (projectId: string, data: { requirement_ids: string[]; review_status: 'CONFIRMED' | 'REJECTED'; review_note?: string }) =>
    request.patch<Requirement[]>(`/projects/${projectId}/requirements/bulk-review`, data) as unknown as Promise<Requirement[]>,
}

// 风险
export const riskApi = {
  run: (projectId: string) =>
    request.post<Task>(`/projects/${projectId}/risks/run`) as unknown as Promise<Task>,

  list: (projectId: string) =>
    request.get<Risk[]>(`/projects/${projectId}/risks`) as unknown as Promise<Risk[]>,

  review: (projectId: string, id: string, data: { status: string; resolution: string }) =>
    request.patch<Risk>(`/projects/${projectId}/risks/${id}`, data) as unknown as Promise<Risk>,
}

// 企业材料
export const materialApi = {
  list: (params?: { enterprise_id?: string }) =>
    request.get<EnterpriseMaterial[]>('/enterprise-materials', { params }) as unknown as Promise<EnterpriseMaterial[]>,

  create: (data: any) =>
    request.post<EnterpriseMaterial>('/enterprise-materials', data) as unknown as Promise<EnterpriseMaterial>,

  update: (id: string, data: Partial<EnterpriseMaterial>) =>
    request.patch<EnterpriseMaterial>(`/enterprise-materials/${id}`, data) as unknown as Promise<EnterpriseMaterial>,

  delete: (id: string) =>
    request.delete(`/enterprise-materials/${id}`),

  uploadDocument: (materialId: string, file: File) => {
    const formData = new FormData()
    formData.append('file', file)
    return request.post(`/enterprise-materials/${materialId}/documents`, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  attachDocument: (materialId: string, data: { document_id: string; document_version_id: string }) =>
    request.post<EnterpriseMaterial>(`/enterprise-materials/${materialId}/documents/attach`, data) as unknown as Promise<EnterpriseMaterial>,
}

// 匹配
export const matchApi = {
  run: (projectId: string) =>
    request.post<Task>(`/projects/${projectId}/matches/run`) as unknown as Promise<Task>,

  list: (projectId: string) =>
    request.get<MatchResult[]>(`/projects/${projectId}/matches`) as unknown as Promise<MatchResult[]>,

  override: (projectId: string, id: string, data: { final_status: string; reason: string }) =>
    request.patch<MatchResult>(`/projects/${projectId}/matches/${id}`, data) as unknown as Promise<MatchResult>,
}

// 决策
export const decisionApi = {
  generate: (projectId: string) =>
    request.post<Task>(`/projects/${projectId}/decision/generate`) as unknown as Promise<Task>,

  get: (projectId: string) =>
    request.get<Decision | null>(`/projects/${projectId}/decision`) as unknown as Promise<Decision | null>,
}

// 报告
export const reportApi = {
  generate: (projectId: string) =>
    request.post<Task>(`/projects/${projectId}/reports`) as unknown as Promise<Task>,

  // 后端返回最新一份报告，没有则为 null
  latest: (projectId: string) =>
    request.get<Report | null>(`/projects/${projectId}/reports`) as unknown as Promise<Report | null>,

  get: (id: string) =>
    request.get<Report>(`/reports/${id}`) as unknown as Promise<Report>,

  // 后端直接流式返回文件，需要带 token 走 blob 下载
  download: async (id: string, format: 'docx' | 'pdf' | 'md') => {
    const token = localStorage.getItem('access_token')
    const res = await fetch(`/api/v1/reports/${id}/download?format=${format}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
    if (!res.ok) {
      throw new Error(`下载失败（HTTP ${res.status}）`)
    }
    const blob = await res.blob()
    const disposition = res.headers.get('Content-Disposition') || ''
    const fileName = /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? `report.${format}`
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    link.click()
    URL.revokeObjectURL(url)
  },
}

// 任务
export const taskApi = {
  get: (id: string) =>
    request.get<Task>(`/tasks/${id}`) as unknown as Promise<Task>,
}

// 规则
export const ruleApi = {
  list: (params?: { page?: number; page_size?: number; status?: string }) =>
    request.get<PaginatedResponse<Rule>>('/rules', { params }) as unknown as Promise<PaginatedResponse<Rule>>,

  create: (data: Partial<Rule>) =>
    request.post<Rule>('/rules', data) as unknown as Promise<Rule>,

  update: (id: string, data: Partial<Rule>) =>
    request.patch<Rule>(`/rules/${id}`, data) as unknown as Promise<Rule>,
}

// 审计日志
export const auditApi = {
  list: (params?: { page?: number; page_size?: number; project_id?: string }) =>
    request.get<PaginatedResponse<AuditLog>>('/audit-logs', { params }) as unknown as Promise<PaginatedResponse<AuditLog>>,
}

// 问答
export const chatApi = {
  askStream: (projectId: string | undefined, question: string, sessionId?: string): Promise<ReadableStream> => {
    const token = localStorage.getItem('access_token')
    return fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ question, project_id: projectId, session_id: sessionId }),
    }).then(res => res.body!)
  },

  // 会话 CRUD
  getSessions: (page = 1, pageSize = 20) =>
    request.get<{ items: any[]; total: number; page: number; page_size: number }>('/sessions', { params: { page, page_size: pageSize } }) as unknown as Promise<{ items: any[]; total: number; page: number; page_size: number }>,

  createSession: (data: { title?: string; project_id?: string } = {}) =>
    request.post<any>('/sessions', data) as unknown as Promise<any>,

  getSession: (id: string) =>
    request.get<any>(`/sessions/${id}`) as unknown as Promise<any>,

  updateSession: (id: string, data: { title: string }) =>
    request.put<any>(`/sessions/${id}`, data) as unknown as Promise<any>,

  deleteSession: (id: string) =>
    request.delete(`/sessions/${id}`) as unknown as Promise<void>,

  getMessages: (id: string, limit = 50) =>
    request.get<{ items: any[]; total: number }>(`/sessions/${id}/messages`, { params: { limit } }) as unknown as Promise<{ items: any[]; total: number }>,

  createMessage: (sessionId: string, data: { role: string; content: string; knowledge_references?: any; is_fallback?: boolean }) =>
    request.post<any>(`/sessions/${sessionId}/messages`, data) as unknown as Promise<any>,
}

// 知识库
export const knowledgeApi = {
  list: (query?: string) =>
    request.get<KnowledgeEntry[]>('/knowledge-entries', query ? { params: { query } } : {}) as unknown as Promise<KnowledgeEntry[]>,

    create: (data: { title: string; content: string; knowledge_type: string; source_reference: string; authority?: string; issued_on?: string; effective_on?: string; citation_note?: string }) =>
    request.post<KnowledgeEntry>('/knowledge-entries', data) as unknown as Promise<KnowledgeEntry>,

  // 修订内容（生成新版本，状态回到 DRAFT）
  revise: (entryId: string, data: { content: string; issued_on?: string | null; effective_on?: string | null; citation_note?: string | null }) =>
    request.post<KnowledgeEntry>(`/knowledge-entries/${entryId}/versions`, data) as unknown as Promise<KnowledgeEntry>,

  publish: (versionId: string) =>
    request.post<KnowledgeEntry>(`/knowledge-versions/${versionId}/publish`),

  unpublish: (versionId: string) =>
    request.post<KnowledgeEntry>(`/knowledge-versions/${versionId}/unpublish`),

  delete: (entryId: string) =>
    request.delete(`/knowledge-entries/${entryId}`),

  uploadDocument: (
    entryId: string,
    file: File,
    knowledgeType: string,
      extra: { title?: string; source_reference?: string; authority?: string; issued_on?: string; effective_on?: string; citation_note?: string } = {}
  ) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('knowledge_type', knowledgeType)
    if (extra.title) formData.append('title', extra.title)
    if (extra.source_reference) formData.append('source_reference', extra.source_reference)
    if (extra.authority) formData.append('authority', extra.authority)
    if (extra.issued_on) formData.append('issued_on', extra.issued_on)
      if (extra.effective_on) formData.append('effective_on', extra.effective_on)
      if (extra.citation_note) formData.append('citation_note', extra.citation_note)
    const url = entryId
      ? `/knowledge-entries/${entryId}/documents`
      : '/knowledge-entries/documents'
    return request.post(url, formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  askStream: (question: string, projectId?: string, sessionId?: string): Promise<ReadableStream> => {
    const token = localStorage.getItem('access_token')
    return fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify({ question, project_id: projectId, session_id: sessionId }),
    }).then(res => res.body!)
  },
}

export interface EvaluationCaseInput {
  question: string
  scope: 'knowledge' | 'project'
  expected_evidence: string[]
}

export interface EvaluationSet {
  id: string
  name: string
  description: string | null
  enabled: boolean
  version: number
  cases: Array<EvaluationCaseInput & { id: string }>
}

export interface EvaluationSetPayload {
  name: string
  description?: string | null
  cases: EvaluationCaseInput[]
}

export interface EvaluationRunResult {
  total: number
  passed: number
  skipped: number
  recall_at_5: number
  elapsed_ms: number
  results: Array<{
    question: string
    scope: string
    passed: boolean | null
    skipped?: boolean
    rank?: number
    error?: string
    expected?: string[]
    matched_excerpt?: string
  }>
}

export const evaluationApi = {
  listSets: () =>
    request.get<EvaluationSet[]>('/evaluations/sets') as unknown as Promise<EvaluationSet[]>,

  createSet: (data: EvaluationSetPayload) =>
    request.post<EvaluationSet>('/evaluations/sets', data) as unknown as Promise<EvaluationSet>,

  updateSet: (id: string, data: EvaluationSetPayload) =>
    request.put<EvaluationSet>(`/evaluations/sets/${id}`, data) as unknown as Promise<EvaluationSet>,

  setEnabled: (id: string, enabled: boolean) =>
    request.patch<EvaluationSet>(`/evaluations/sets/${id}/enabled`, null, { params: { enabled } }) as unknown as Promise<EvaluationSet>,

  deleteSet: (id: string) =>
    request.delete(`/evaluations/sets/${id}`) as unknown as Promise<void>,

  runRag: (projectId?: string, setId?: string) =>
    request.post<EvaluationRunResult>('/evaluations/rag', null, {
      params: { ...(projectId ? { project_id: projectId } : {}), ...(setId ? { set_id: setId } : {}) },
    }) as unknown as Promise<EvaluationRunResult>,
}

// Agent Run（投标分析）
// 注意：除 create/list 外，后端详情与操作路由均不带 project_id 前缀
export const agentApi = {
  create: (projectId: string, data: { workflow: AgentWorkflow; goal: string; document_version_id?: string }) =>
    request.post<AgentRun>(`/projects/${projectId}/agent-runs`, data) as unknown as Promise<AgentRun>,

  list: (projectId: string) =>
    request.get<AgentRun[]>(`/projects/${projectId}/agent-runs`) as unknown as Promise<AgentRun[]>,

  get: (runId: string) =>
    request.get<AgentRun>(`/agent-runs/${runId}`) as unknown as Promise<AgentRun>,

  retry: (runId: string) =>
    request.post<AgentRun>(`/agent-runs/${runId}/retry`) as unknown as Promise<AgentRun>,

  review: (runId: string, data: { approved: boolean; note: string }) =>
    request.post<AgentRun>(`/agent-runs/${runId}/review`, data) as unknown as Promise<AgentRun>,

  adoptRecommendation: (runId: string, recommendationId: string, note: string) =>
    request.post<AgentRecommendation>(`/agent-runs/${runId}/recommendations/${recommendationId}/adopt`, { note }) as unknown as Promise<AgentRecommendation>,
}

// 投标分析（bid_pipeline，新链路：上传走 documentApi，报告按 version 查询）
export interface BidReport {
  version_id?: string
  decision: string
  overall_score: number
  qualification_score: number
  risk_score: number
  trap_score: number
  competition_score: number
  summary: string
  report_md: string
  report_json?: Record<string, any>
}

export interface BidReportCard {
  version_id: string
  doc_name: string
  decision: string
  overall_score: number
  summary: string
  report_md: string | null
  created_at: string | null
  parse_status?: string
}
