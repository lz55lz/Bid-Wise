<template>
  <div class="project-detail">
    <!-- 顶部信息栏 -->
    <div class="project-header">
      <div class="header-left">
        <el-button type="primary" size="small" :icon="ArrowLeft" @click="router.push('/projects')">
          返回
        </el-button>
        <div class="project-title">
          <h1>{{ project?.name }}</h1>
          <div class="project-meta">
            <span class="project-code">{{ project?.code }}</span>
            <span :class="['badge', `badge-${getStatusClass(project?.status)}`]">
              {{ getStatusText(project?.status) }}
            </span>
          </div>
        </div>
      </div>
      <div class="header-actions">
        <el-button v-if="project?.status !== 'ARCHIVED'" :icon="Upload" @click="showUploadDialog = true">
          上传文档
        </el-button>
        <el-button
          v-if="project?.status !== 'ARCHIVED'"
          :icon="MoreFilled"
          @click="handleArchive"
        >
          归档项目
        </el-button>
      </div>
    </div>

    <!-- 项目信息卡片 -->
    <div class="info-cards">
      <div class="info-card">
        <span class="info-label">招标人</span>
        <span class="info-value">{{ project?.purchaser }}</span>
      </div>
      <div class="info-card">
        <span class="info-label">项目类型</span>
        <span class="info-value">{{ getTypeText(project?.project_type) }}</span>
      </div>
      <div class="info-card">
        <span class="info-label">地区</span>
        <span class="info-value">{{ project?.region }}</span>
      </div>
      <div class="info-card">
        <span class="info-label">投标截止</span>
        <span class="info-value" :class="{ overdue: isOverdue(project?.bid_deadline) }">
          {{ formatDate(project?.bid_deadline) }}
        </span>
      </div>
    </div>

    <section class="analysis-run-section">
      <div class="section-heading">
        <div>
          <h2 class="section-title">统一投标分析</h2>
          <p>冻结当前文件、材料和规则版本后，依次完成匹配、风险、决策与报告。</p>
        </div>
        <div class="analysis-actions">
          <el-button v-if="latestAnalysisRun" text @click="openSnapshot(latestAnalysisRun.id)">查看分析快照</el-button>
          <el-button v-if="project?.status !== 'ARCHIVED' && latestAnalysisRun" type="primary" :loading="analysisSubmitting" :disabled="analysisIsActive" @click="startUnifiedAnalysis">
            {{ analysisIsActive ? '分析进行中' : '重新分析' }}
          </el-button>
        </div>
      </div>
      <div v-if="latestAnalysisRun" class="analysis-run-card">
        <div class="run-summary">
          <el-tag :type="analysisTagType(latestAnalysisRun.status)">{{ analysisStatusText(latestAnalysisRun.status) }}</el-tag>
          <span>当前阶段：{{ analysisStageText(latestAnalysisRun.current_stage) }}</span>
          <span>创建于 {{ formatDate(latestAnalysisRun.created_at) }}</span>
        </div>
        <el-alert v-if="latestAnalysisRun.status === 'FAILED'" type="error" :title="latestAnalysisRun.error_message || '分析运行失败'" :closable="false" />
        <div class="analysis-timeline">
          <div v-for="stage in analysisStages" :key="stage.key" class="analysis-stage">
            <el-icon :class="`analysis-stage-${stage.status}`">
              <CircleCheckFilled v-if="stage.status === 'completed'" />
              <Loading v-else-if="stage.status === 'processing'" class="is-loading" />
              <WarningFilled v-else-if="stage.status === 'failed'" />
              <Clock v-else />
            </el-icon>
            <span>{{ stage.label }}<small v-if="stage.status === 'skipped'">（无需复核）</small></span>
          </div>
        </div>
      </div>
      <div v-else class="empty-state-small">完成招标文件解析后，即可发起统一分析。</div>
    </section>

    <!-- 业务进度：技术细节仅保留在任务日志，避免与人工复核状态混淆 -->
    <div class="stages-section">
      <h2 class="section-title">投标准备进度</h2>
      <div class="pipeline-stages">
        <div
          v-for="(stage, idx) in businessStages"
          :key="stage.key"
          class="stage-item"
          :class="[`stage-${stage.status}`, { 'stage-active': stage.status === 'processing' }]"
        >
          <div class="stage-indicator">
            <div v-if="stage.status === 'completed'" class="stage-check">
              <el-icon><Check /></el-icon>
            </div>
            <div v-else-if="stage.status === 'processing'" class="stage-spinner">
              <el-icon class="is-loading"><Loading /></el-icon>
            </div>
            <div v-else-if="stage.status === 'failed'" class="stage-error">
              <el-icon><Close /></el-icon>
            </div>
            <div v-else class="stage-dot" />
          </div>
          <div class="stage-info">
            <span class="stage-name">{{ stage.label }}</span>
            <span class="stage-desc">{{ stage.description }}</span>
          </div>
          <div v-if="idx < businessStages.length - 1" class="stage-connector" />
        </div>
      </div>
      <div v-if="canStartAnalysis" class="next-step-action">
        <div>
          <strong>关键需求已确认</strong>
          <span>下一步将冻结当前文件、企业材料和规则版本，并执行匹配、风险、决策与报告生成。</span>
        </div>
        <el-button type="primary" :loading="analysisSubmitting" @click="startUnifiedAnalysis">下一步：开始匹配分析</el-button>
      </div>
    </div>

    <!-- Tab 切换 -->
    <el-tabs v-model="activeTab" class="project-tabs">
      <el-tab-pane label="文档" name="documents">
        <div class="tab-header">
          <h3>项目文档</h3>
          <el-button v-if="project?.status !== 'ARCHIVED'" type="primary" size="small" @click="showUploadDialog = true">
            上传文档
          </el-button>
        </div>

        <div v-if="bidDocs.length" class="card-grid">
          <div v-for="doc in bidDocs" :key="doc.doc_id" class="card doc-card" @click="router.push(`/projects/${projectId}/documents/${doc.doc_id}`)">
            <div class="card-icon">
              <el-icon><Document /></el-icon>
            </div>
            <div class="card-body">
              <div class="card-title">{{ doc.doc_name }}</div>
              <div class="card-meta">
                <span :class="['badge', `badge-${getDocStatusClass(doc.parse_status)}`]">
                  {{ getDocStatusText(doc.parse_status) }}
                </span>
                <span v-if="doc.created_at" class="card-date">{{ formatDate(doc.created_at) }}</span>
              </div>
            </div>
            <el-button size="small" class="card-action" @click.stop="router.push(`/projects/${projectId}/documents/${doc.doc_id}`)">
              查看文档
            </el-button>
          </div>
        </div>

        <div v-else class="empty-state-small">
          <p>暂无文档，请上传招标文件开始分析</p>
        </div>
      </el-tab-pane>

      <el-tab-pane label="需求复核" name="requirements">
        <div class="tab-header">
          <div>
            <h3>需求复核工作台</h3>
            <p class="tab-tip">仅处理高价值不确定性；可先查看招标原文，再确认是否进入企业材料匹配。</p>
          </div>
          <el-button size="small" @click="loadRequirements">刷新</el-button>
        </div>
        <template v-if="requirements.length">
        <div class="review-metrics">
          <span>优先复核 <strong>{{ priorityRequirements.length }}</strong></span>
          <span>延后复核 <strong>{{ deferredRequirements.length }}</strong></span>
          <span>已确认 <strong>{{ confirmedRequirements.length }}</strong></span>
        </div>
        <div class="review-queue-title"><div><h4>优先复核队列</h4><small>按强制信号、类别、置信度与证据完整性排序</small></div><div><el-button size="small" type="success" :disabled="!selectedPriorityRequirements.length" @click="bulkReview('CONFIRMED')">批量确认</el-button><el-button size="small" :disabled="!selectedPriorityRequirements.length" @click="bulkReview('REJECTED')">批量驳回</el-button></div></div>
        <el-table :data="priorityRequirements" size="small" max-height="400" @selection-change="selectedPriorityRequirements = $event">
          <el-table-column type="selection" width="42" />
          <el-table-column prop="title" label="需求" min-width="240" show-overflow-tooltip />
          <el-table-column label="风险信号" width="155"><template #default="{ row }"><el-tag v-if="row.is_mandatory" size="small" type="danger">强制</el-tag> {{ row.category }}</template></el-table-column>
          <el-table-column label="置信度 / 证据" width="130">
            <template #default="{ row }">{{ formatConfidence(row.confidence) }} / {{ row.evidence_ids.length }} 条</template>
          </el-table-column>
          <el-table-column label="操作" width="230">
            <template #default="{ row }">
              <el-button size="small" text :disabled="!row.evidence_ids.length" @click="openRequirementEvidence(row)">原文依据</el-button>
              <template v-if="row.review_status === 'PENDING'">
                <el-button size="small" type="success" :disabled="!row.evidence_ids.length" @click="reviewRequirement(row, 'CONFIRMED')">确认</el-button>
                <el-button size="small" text @click="reviewRequirement(row, 'REJECTED')">驳回</el-button>
              </template>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="deferredRequirements.length" class="review-queue-title deferred-queue"><div><h4>延后复核（{{ deferredRequirements.length }}）</h4><small>有效候选已保留，关键队列完成后再处理。</small></div></div>
        <el-table v-if="deferredRequirements.length" :data="deferredRequirements" size="small" max-height="250">
          <el-table-column prop="title" label="需求" min-width="240" show-overflow-tooltip />
          <el-table-column prop="category" label="类别" width="120" />
          <el-table-column label="置信度 / 证据" width="130"><template #default="{ row }">{{ formatConfidence(row.confidence) }} / {{ row.evidence_ids.length }} 条</template></el-table-column>
          <el-table-column label="操作" width="205"><template #default="{ row }"><el-button size="small" text :disabled="!row.evidence_ids.length" @click="openRequirementEvidence(row)">原文依据</el-button><el-button size="small" @click="promoteRequirement(row)">提升优先级</el-button></template></el-table-column>
        </el-table>
        </template>
        <div v-else class="empty-state-small">文档解析完成后将自动生成待复核的 Requirement。</div>
      </el-tab-pane>

      <el-tab-pane label="报告" name="reports">
        <div v-if="latestReport" class="card-grid">
          <div class="card report-card">
            <div>
              <div class="card-title">{{ project?.name }}</div>
              <div class="card-date">v{{ latestReport.version_no }} · {{ latestReport.status }}</div>
            </div>
            <el-button size="small" type="primary" class="card-action" :disabled="latestReport.status !== 'READY'" @click="openReportPreview">查看报告</el-button>
          </div>
        </div>

        <div v-else class="empty-state-small">
          <p>暂无统一分析报告，请先确认关键需求并发起统一分析。</p>
        </div>
      </el-tab-pane>

    </el-tabs>

    <el-dialog v-model="showSnapshotDialog" title="分析快照" width="760px" destroy-on-close>
      <template v-if="selectedAnalysisRun?.snapshot">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="招标文件版本">{{ selectedAnalysisRun.snapshot.tender_version_ids.length }} 份</el-descriptions-item>
          <el-descriptions-item label="企业材料">{{ selectedAnalysisRun.snapshot.enterprise_material_ids.length }} 项</el-descriptions-item>
          <el-descriptions-item label="启用规则版本">{{ selectedAnalysisRun.snapshot.rule_version_ids.length }} 条</el-descriptions-item>
        </el-descriptions>
        <el-timeline class="snapshot-timeline">
          <el-timeline-item v-for="(output, stage) in selectedAnalysisRun.snapshot.stage_outputs" :key="stage" :type="output.status === 'SUCCEEDED' ? 'success' : output.status === 'FAILED' ? 'danger' : 'primary'">
            <strong>{{ analysisStageText(stage) }}</strong>
            <span class="snapshot-output">{{ formatStageOutput(output) }}</span>
          </el-timeline-item>
        </el-timeline>
      </template>
      <el-empty v-else description="快照加载中" />
    </el-dialog>

    <el-dialog v-model="showEvidenceDialog" title="需求原文依据" width="760px" destroy-on-close>
      <template v-if="selectedRequirement">
        <el-descriptions :column="1" border class="evidence-summary">
          <el-descriptions-item label="需求标题">{{ selectedRequirement.title }}</el-descriptions-item>
          <el-descriptions-item label="提取结论">{{ selectedRequirement.description || '未生成补充说明' }}</el-descriptions-item>
        </el-descriptions>
        <div v-loading="evidenceLoading" class="evidence-list">
          <el-empty v-if="!evidenceLoading && !selectedEvidence.length" description="未找到可展示的原文依据" />
          <article v-for="evidence in selectedEvidence" :key="evidence.id" class="evidence-card">
            <div class="evidence-meta">原文依据 · 第 {{ evidence.page_number || '-' }} 页</div>
            <blockquote>{{ evidence.quoted_text || '原文节选不可用' }}</blockquote>
          </article>
        </div>
      </template>
    </el-dialog>

    <el-dialog v-model="showReportPreview" title="投标分析报告" width="min(1080px, 94vw)" top="4vh" destroy-on-close>
      <template #header>
        <div class="report-preview-header">
          <div><strong>投标分析报告</strong><span v-if="previewReport">v{{ previewReport.version_no }} · {{ formatDate(previewReport.generated_at || previewReport.created_at) }}</span></div>
          <div v-if="previewReport"><el-button size="small" @click="downloadReport('md')">下载 MD</el-button><el-button size="small" type="primary" @click="downloadReport('pdf')">下载 PDF</el-button></div>
        </div>
      </template>
      <div v-loading="reportPreviewLoading" class="report-preview">
        <el-empty v-if="!reportPreviewLoading && !(previewReport?.sections?.length ?? 0)" description="报告内容暂不可用" />
        <section v-for="section in (previewReport?.sections || [])" :key="section.section_code" class="report-preview-section">
          <div class="report-preview-section-title"><h3>{{ reportSectionName(section.section_code) }}</h3><span>{{ section.evidence_ids.length }} 条原文依据</span></div>
          <MarkdownRenderer :content="section.content_markdown" />
        </section>
      </div>
    </el-dialog>

    <!-- 上传文档对话框 -->
    <el-dialog v-model="showUploadDialog" title="上传文档" width="500px">
      <el-form ref="uploadFormRef" :model="uploadForm" label-width="100px">
        <el-form-item label="文档类型" prop="document_type">
          <el-select v-model="uploadForm.document_type" style="width: 100%">
            <el-option label="招标文件" value="TENDER" />
          </el-select>
        </el-form-item>

        <el-form-item label="选择文件">
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            :on-change="handleFileChange"
            drag
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <span>将文件拖到此处，或<span class="upload-link">点击上传</span></span>
            <template #tip>
              <div class="upload-tip">支持 PDF、DOCX、XLSX、PPTX、JPG、PNG 格式，单文件不超过 50MB</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showUploadDialog = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="handleUpload">上传</el-button>
      </template>
    </el-dialog>

  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useProjectStore } from '@/stores'
import { analysisApi, documentApi, evidenceApi, reportApi, requirementApi } from '@/api'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, Upload, MoreFilled, Check, Close, Loading, Document, UploadFilled,
  CircleCheckFilled, WarningFilled, Clock
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance } from 'element-plus'
import type { AnalysisRun, BidDocumentCard, Project, Evidence, Report, Requirement } from '@/types'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'

const router = useRouter()
const route = useRoute()
const projectStore = useProjectStore()

const projectId = route.params.id as string

const project = ref<Project | null>(null)
const analysisRuns = ref<AnalysisRun[]>([])
const selectedAnalysisRun = ref<AnalysisRun | null>(null)
const analysisSubmitting = ref(false)
const showSnapshotDialog = ref(false)
const analysisPollTimer = ref<number | null>(null)
const latestAnalysisRun = computed(() => analysisRuns.value[0] ?? null)
const analysisIsActive = computed(() => ['QUEUED', 'RUNNING'].includes(latestAnalysisRun.value?.status || ''))
const analysisStages = computed(() => buildAnalysisStages(latestAnalysisRun.value))
// 文档列表
const bidDocs = ref<BidDocumentCard[]>([])
const requirements = ref<Requirement[]>([])
const selectedPriorityRequirements = ref<Requirement[]>([])
const priorityRequirements = computed(() => requirements.value.filter(item => item.review_status === 'PENDING'))
const deferredRequirements = computed(() => requirements.value.filter(item => item.review_status === 'DEFERRED'))
const confirmedRequirements = computed(() => requirements.value.filter(item => item.review_status === 'CONFIRMED'))
const latestReport = ref<Report | null>(null)
const showReportPreview = ref(false)
const reportPreviewLoading = ref(false)
const previewReport = ref<Report | null>(null)
const showEvidenceDialog = ref(false)
const evidenceLoading = ref(false)
const selectedRequirement = ref<Requirement | null>(null)
const selectedEvidence = ref<Evidence[]>([])
const documentPollTimer = ref<number | null>(null)

const businessStages = computed(() => {
  const documentReady = bidDocs.value.some(doc => doc.parse_status === 'READY')
  const documentFailed = bidDocs.value.some(doc => doc.parse_status === 'FAILED')
  const outstandingPriorityReview = priorityRequirements.value.length > 0
  const reviewComplete = requirements.value.length > 0 && !outstandingPriorityReview
  const run = latestAnalysisRun.value
  const report = latestReport.value
  const analysisCurrent = run?.status === 'SUCCEEDED' && reviewComplete
  const reportCurrent = report?.status === 'READY' && reviewComplete
  return [
    { key: 'document', label: '文档处理', description: documentFailed ? '存在解析失败文档' : documentReady ? '已完成解析与结构化' : bidDocs.value.length ? '文档处理中' : '等待招标文件', status: documentFailed ? 'failed' : documentReady ? 'completed' : bidDocs.value.length ? 'processing' : 'pending' },
    { key: 'review', label: '需求复核', description: reviewComplete ? (deferredRequirements.value.length ? `关键需求已确认；${deferredRequirements.value.length} 条低优先候选待后续处理` : '关键需求已确认') : outstandingPriorityReview ? `待确认 ${priorityRequirements.value.length} 条关键需求` : '等待需求生成', status: reviewComplete ? 'completed' : documentReady ? 'processing' : 'pending' },
    { key: 'analysis', label: '匹配分析', description: analysisCurrent ? '匹配、风险与决策已完成' : run?.status === 'SUCCEEDED' ? '需求变更，需完成复核后重新分析' : run?.status === 'WAITING_HUMAN' ? '等待分析复核' : run ? '分析进行中' : reviewComplete ? '可发起匹配分析' : '等待需求复核完成', status: analysisCurrent ? 'completed' : run?.status === 'SUCCEEDED' ? 'pending' : run ? 'processing' : 'pending' },
    { key: 'report', label: '报告生成', description: reportCurrent ? '报告已生成' : report?.status === 'READY' ? '需求变更，现有报告待更新' : report?.status === 'GENERATING' ? '报告生成中' : '等待匹配分析完成', status: reportCurrent ? 'completed' : report?.status === 'READY' ? 'pending' : report?.status === 'GENERATING' ? 'processing' : 'pending' },
  ]
})

const canStartAnalysis = computed(() => {
  const documentReady = bidDocs.value.some(doc => doc.parse_status === 'READY')
  const documentFailed = bidDocs.value.some(doc => doc.parse_status === 'FAILED')
  return documentReady
    && !documentFailed
    && requirements.value.length > 0
    && priorityRequirements.value.length === 0
    && !latestAnalysisRun.value
    && project.value?.status !== 'ARCHIVED'
})

const activeTab = ref('documents')

// 上传
const showUploadDialog = ref(false)
const uploading = ref(false)
const uploadFormRef = ref<FormInstance>()
const uploadRef = ref()
const uploadForm = reactive({
  document_type: 'TENDER',
  file: null as File | null,
})

const handleArchive = async () => {
  try {
    await ElMessageBox.confirm('确定要归档该项目吗？', '提示', { type: 'warning' })
    await projectStore.archiveProject(projectId)
    ElMessage.success('项目已归档')
    router.push('/projects')
  } catch {}
}

const handleFileChange = (file: any) => {
  uploadForm.file = file.raw
}

const handleUpload = async () => {
  if (!uploadForm.file) {
    ElMessage.warning('请选择文件')
    return
  }

  uploading.value = true
  try {
    // 上传即自动触发 bid_pipeline（后端入队 run_bid_pipeline）
    await documentApi.upload(projectId, uploadForm.file, 'TENDER')
    ElMessage.success('上传成功，分析管线已启动')
    showUploadDialog.value = false
    uploadForm.file = null
    activeTab.value = 'reports'
    await loadDocuments()
    startDocumentPolling()
  } catch (e: any) {
    ElMessage.error(e?.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

// 文档解析期间仅刷新文档事实状态；不再把内部 Worker 步骤伪装成业务进度。
const startDocumentPolling = () => {
  stopDocumentPolling()
  documentPollTimer.value = window.setInterval(async () => {
    try {
      await loadDocuments()
      const isProcessing = bidDocs.value.some(doc =>
        ['UPLOADED', 'QUEUED', 'RUNNING', 'PARSING', 'PARSED', 'STRUCTURING', 'INDEXING'].includes(doc.parse_status),
      )
      if (!isProcessing) {
        stopDocumentPolling()
        await Promise.all([loadRequirements(), loadReports()])
      }
    } catch {
      // 下一次轮询会重试，避免短暂网络异常中断页面状态刷新。
    }
  }, 3000)
}

const stopDocumentPolling = () => {
  if (documentPollTimer.value) {
    clearInterval(documentPollTimer.value)
    documentPollTimer.value = null
  }
}

// ---------------------------------------------------------------------------
// 数据加载
// ---------------------------------------------------------------------------

const loadDocuments = async () => {
  bidDocs.value = await documentApi.list(projectId)
}

const loadReports = async () => {
  latestReport.value = await reportApi.latest(projectId)
}

const loadRequirements = async () => {
  requirements.value = await requirementApi.list(projectId)
  selectedPriorityRequirements.value = []
}

const openRequirementEvidence = async (item: any) => {
  const requirement = item as Requirement
  selectedRequirement.value = requirement
  selectedEvidence.value = []
  showEvidenceDialog.value = true
  evidenceLoading.value = true
  try {
    selectedEvidence.value = await Promise.all(requirement.evidence_ids.map(id => evidenceApi.get(id)))
  } catch (error: any) {
    ElMessage.error(error?.message || '原文依据加载失败')
  } finally {
    evidenceLoading.value = false
  }
}

const reviewRequirement = async (item: any, reviewStatus: 'CONFIRMED' | 'REJECTED') => {
  try {
    await requirementApi.review(projectId, item.id, {
      review_status: reviewStatus,
      review_note: reviewStatus === 'CONFIRMED' ? '已核对来源证据' : '不作为本项目有效要求',
    })
    await loadRequirements()
    ElMessage.success(reviewStatus === 'CONFIRMED' ? '需求已确认，可用于统一分析' : '需求已驳回')
  } catch (error: any) {
    ElMessage.error(error?.message || '需求复核失败')
  }
}

const bulkReview = async (reviewStatus: 'CONFIRMED' | 'REJECTED') => {
  const items = selectedPriorityRequirements.value
  if (!items.length) return
  try {
    await ElMessageBox.confirm(`确定${reviewStatus === 'CONFIRMED' ? '确认' : '驳回'} ${items.length} 条需求吗？`, '批量复核', { type: 'warning' })
    await requirementApi.bulkReview(projectId, { requirement_ids: items.map(item => item.id), review_status: reviewStatus, review_note: reviewStatus === 'CONFIRMED' ? '已批量核对来源证据' : '批量复核后不作为有效要求' })
    await loadRequirements()
    ElMessage.success(`已批量${reviewStatus === 'CONFIRMED' ? '确认' : '驳回'} ${items.length} 条需求`)
  } catch (error: any) {
    if (error !== 'cancel') ElMessage.error(error?.message || '批量复核失败')
  }
}

const promoteRequirement = async (item: any) => {
  try {
    await requirementApi.review(projectId, item.id, { review_status: 'PENDING', review_note: '人工提升至优先复核队列' })
    await loadRequirements()
    ElMessage.success('已提升至优先复核队列')
  } catch (error: any) {
    ElMessage.error(error?.message || '提升优先级失败')
  }
}

const formatConfidence = (value: number | null) => value === null ? '-' : `${Math.round(value * 100)}%`

const downloadReport = async (format: 'pdf' | 'md') => {
  if (!latestReport.value) return
  try {
    await reportApi.download(latestReport.value.id, format)
  } catch (error: any) {
    ElMessage.error(error?.message || '报告下载失败')
  }
}

const openReportPreview = async () => {
  if (!latestReport.value) return
  showReportPreview.value = true
  reportPreviewLoading.value = true
  try {
    previewReport.value = await reportApi.get(latestReport.value.id)
  } catch (error: any) {
    ElMessage.error(error?.message || '报告加载失败')
  } finally {
    reportPreviewLoading.value = false
  }
}

const reportSectionName = (code: string) => ({
  PROJECT_OVERVIEW: '项目概况', BID_SCHEDULE: '关键时间与递交清单',
  QUALIFICATION_MATRIX: '资格条件与企业符合情况', ENTERPRISE_OVERVIEW: '企业概况',
  EXECUTIVE_SUMMARY: '执行摘要', ANALYSIS_COVERAGE: '分析覆盖率',
  CORE_RISKS: '核心风险', KEY_GAPS: '关键缺口', ACTION_PLAN: '行动计划',
  MATERIAL_SUMMARY: '企业材料汇总', MATERIAL_LINKED: '已关联材料', MATERIAL_UNLINKED: '待补充材料',
  QUALIFICATION_ANALYSIS: '资格分析', RISK_ITEMS: '全部风险事项',
  ENTERPRISE_MATCHING: '企业匹配', SCORING_ANALYSIS: '评分要点',
  COMPREHENSIVE_DECISION: '综合决策', TODOS: '待办事项',
} as Record<string, string>)[code] || code

const loadAnalysisRuns = async () => {
  analysisRuns.value = await analysisApi.list(projectId)
}

const startUnifiedAnalysis = async () => {
  analysisSubmitting.value = true
  try {
    await analysisApi.run(projectId)
    await loadAnalysisRuns()
    startAnalysisPolling()
    ElMessage.success('统一分析已进入队列')
  } catch (error: any) {
    ElMessage.error(error?.message || '无法发起分析，请先确认招标文件已解析完成')
  } finally {
    analysisSubmitting.value = false
  }
}

const openSnapshot = async (runId: string) => {
  showSnapshotDialog.value = true
  try {
    selectedAnalysisRun.value = await analysisApi.get(runId)
  } catch (error: any) {
    ElMessage.error(error?.message || '分析快照加载失败')
  }
}

const startAnalysisPolling = () => {
  stopAnalysisPolling()
  analysisPollTimer.value = window.setInterval(async () => {
    await loadAnalysisRuns()
    if (!analysisIsActive.value) {
      stopAnalysisPolling()
      if (latestAnalysisRun.value?.report_id) loadReports()
    }
  }, 3000)
}

const stopAnalysisPolling = () => {
  if (analysisPollTimer.value) {
    clearInterval(analysisPollTimer.value)
    analysisPollTimer.value = null
  }
}

const analysisStatusText = (status: string) => ({
  QUEUED: '排队中', RUNNING: '分析中', WAITING_HUMAN: '等待人工复核',
  SUCCEEDED: '分析完成', FAILED: '分析失败', CANCELLED: '已结束',
} as Record<string, string>)[status] || status

const analysisTagType = (status: string) => ({
  QUEUED: 'info', RUNNING: 'warning', WAITING_HUMAN: 'warning',
  SUCCEEDED: 'success', FAILED: 'danger', CANCELLED: 'info',
} as Record<string, any>)[status] || 'info'

const analysisStageText = (stage: string) => ({
  SNAPSHOT: '冻结分析快照', MATCHING: '企业材料匹配', RISK_CHECK: '规则风险检查',
  DECISION: '确定性投标决策', REPORT_QUEUED: '正式报告生成',
  REPORT_GENERATING: '正式报告生成', REPORT: '正式报告生成',
} as Record<string, string>)[stage] || stage

const buildAnalysisStages = (run: AnalysisRun | null) => {
  const keys = ['SNAPSHOT', 'MATCHING', 'RISK_CHECK', 'DECISION', 'REPORT']
  const current = ['REPORT_QUEUED', 'REPORT_GENERATING'].includes(run?.current_stage || '')
    ? 'REPORT'
    : run?.current_stage
  const currentIndex = keys.indexOf(current || '')
  const outputs = run?.snapshot?.stage_outputs || {}
  return keys.map((key, index) => ({
    key,
    label: analysisStageText(key),
    status: outputs[key]?.status === 'FAILED' ? 'failed'
      : ['SUCCEEDED', 'APPROVED'].includes(outputs[key]?.status) ? 'completed'
      : index === currentIndex && ['FAILED', 'CANCELLED'].includes(run?.status || '') ? 'failed'
      : index < currentIndex || run?.status === 'SUCCEEDED' ? 'completed'
      : index === currentIndex ? 'processing' : 'pending',
  }))
}

const formatStageOutput = (output: Record<string, any>) => {
  if (output.result_count !== undefined) return `已产出 ${output.result_count} 项结果`
  if (output.suggestion) return `建议：${output.suggestion}`
  if (output.report_id) return '报告任务已投递'
  return output.status === 'SUCCEEDED' ? '已完成' : output.status === 'QUEUED' ? '等待执行' : '已冻结'
}

onMounted(async () => {
  const results = await Promise.allSettled([
    projectStore.fetchProject(projectId).then((p) => { project.value = p }),
    loadDocuments(),
    loadReports(),
    loadRequirements(),
    loadAnalysisRuns(),
  ])
  if (results.some((r) => r.status === 'rejected')) {
    ElMessage.warning('部分数据加载失败，可刷新重试')
  }

  if (bidDocs.value.some(doc => ['UPLOADED', 'QUEUED', 'RUNNING', 'PARSING', 'PARSED', 'STRUCTURING', 'INDEXING'].includes(doc.parse_status))) {
    startDocumentPolling()
  }
  if (analysisIsActive.value) startAnalysisPolling()
})

onUnmounted(() => {
  stopDocumentPolling()
  stopAnalysisPolling()
})

// ---------------------------------------------------------------------------
// 辅助函数
// ---------------------------------------------------------------------------

const getStatusClass = (status?: string) => {
  const map: Record<string, string> = { DRAFT: 'draft', ACTIVE: 'active', ARCHIVED: 'pending' }
  return map[status || ''] || 'pending'
}

const getStatusText = (status?: string) => {
  const map: Record<string, string> = { DRAFT: '草稿', ACTIVE: '进行中', ARCHIVED: '已归档' }
  return map[status || ''] || status
}

const getTypeText = (type?: string) => {
  const map: Record<string, string> = { ENGINEERING: '工程建设', GOVERNMENT: '政府采购', ENTERPRISE: '企业采购', OTHER: '其他' }
  return map[type || ''] || type
}

const formatDate = (date?: string | null) => date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-'
const isOverdue = (deadline?: string) => deadline ? dayjs(deadline).isBefore(dayjs()) : false

const getDocStatusClass = (status?: string) => {
  const map: Record<string, string> = { READY: 'active', FAILED: 'failed', PARSING: 'draft' }
  return map[status || ''] || 'draft'
}

const getDocStatusText = (status?: string) => {
  const map: Record<string, string> = { UPLOADED: '已上传', QUEUED: '排队中', PARSING: '解析中', PARSED: '已解析', STRUCTURING: '结构化', INDEXING: '索引中', READY: '就绪', FAILED: '失败' }
  return map[status || ''] || status || '未知'
}

</script>

<style scoped>
.project-detail {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.project-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: var(--spacing-6);
}

.header-left {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-4);
}

.project-title h1 {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  margin-bottom: var(--spacing-2);
}

.project-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.project-code {
  font-family: monospace;
  color: var(--color-text-muted);
}

.header-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: var(--spacing-2);
}

.info-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--spacing-5);
  margin-bottom: var(--spacing-8);
}

.info-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: var(--spacing-4);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-1);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.info-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.info-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.info-value {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.info-value.overdue {
  color: var(--color-destructive);
}

.stages-section {
  margin-bottom: var(--spacing-8);
}

.review-metrics { display: flex; gap: var(--spacing-3); margin-bottom: var(--spacing-4); }
.review-metrics span { padding: var(--spacing-2) var(--spacing-4); border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-surface); color: var(--color-text-secondary); }
.review-metrics strong { color: var(--color-accent); font-size: var(--font-size-lg); margin-left: var(--spacing-1); }
.review-queue-title { display: flex; justify-content: space-between; align-items: center; gap: var(--spacing-3); margin: var(--spacing-4) 0 var(--spacing-2); }
.review-queue-title h4, .review-queue-title small { margin: 0; }
.review-queue-title small { color: var(--color-text-muted); }
.deferred-queue { padding: var(--spacing-3); border-radius: var(--radius-md); background: color-mix(in srgb, var(--color-warning) 10%, transparent); }

.section-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  margin-bottom: var(--spacing-4);
}

.stages-timeline {
  display: flex;
  gap: 0;
}

.stage-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.stage-indicator {
  display: flex;
  align-items: center;
  width: 100%;
}

.stage-dot {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--color-border);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--font-size-sm);
  font-weight: 600;
  z-index: 1;
  flex-shrink: 0;
}

.stage-item.active .stage-dot {
  background: var(--color-success);
  color: white;
}

.stage-item.current .stage-dot {
  background: var(--color-accent);
  color: white;
}

.stage-line {
  flex: 1;
  height: 2px;
  background: var(--color-border);
  margin: 0 var(--spacing-1);
}

.stage-item.active .stage-line {
  background: var(--color-success);
}

.stage-content {
  text-align: center;
  margin-top: var(--spacing-2);
}

.stage-label {
  display: block;
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.stage-desc {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.project-tabs {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--spacing-4);
}

.tab-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-4);
}

.tab-header h3 {
  font-size: var(--font-size-base);
  font-weight: 600;
}

.tab-actions {
  display: flex;
  gap: var(--spacing-2);
}

.documents-list, .requirements-list, .risks-list, .matches-list, .reports-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}

.document-item, .requirement-item, .risk-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  cursor: pointer;
}

.doc-icon {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-md);
  background: var(--color-background);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  color: var(--color-accent);
}

.doc-info {
  flex: 1;
  display: flex;
  flex-direction: column;
}

.doc-name {
  font-weight: 500;
  color: var(--color-text-primary);
}

.doc-meta {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.req-header, .risk-header, .match-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-2);
}

.req-category {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: var(--color-background);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}

.mandatory-tag {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: #FEE2E2;
  border-radius: var(--radius-sm);
  color: #991B1B;
}

.req-title, .risk-title {
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.req-description, .risk-description, .match-reason {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.req-score {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  margin-top: var(--spacing-2);
  font-size: var(--font-size-sm);
  color: var(--color-warning);
}

.severity-tag {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-sm);
  font-weight: 500;
}

.severity-critical { background: #FEE2E2; color: #991B1B; }
.severity-high { background: #FEF3C7; color: #92400E; }
.severity-medium { background: #E0E7FF; color: #3730A3; }
.severity-low { background: #D1FAE5; color: #065F46; }
.severity-info { background: #E0E7FF; color: #3730A3; }

.risk-type {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.match-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--spacing-4);
  background: var(--color-background);
  border-radius: var(--radius-lg);
}

.stat-value {
  font-size: var(--font-size-2xl);
  font-weight: 700;
}

.stat-value.matched { color: var(--color-success); }
.stat-value.partial { color: var(--color-warning); }
.stat-value.missing { color: var(--color-text-muted); }
.stat-value.conflict { color: var(--color-destructive); }

.stat-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.matches-summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--spacing-4);
  margin-bottom: var(--spacing-4);
}

.match-status {
  font-size: var(--font-size-sm);
  font-weight: 500;
}

.status-matched { color: var(--color-success); }
.status-partial { color: var(--color-warning); }
.status-missing { color: var(--color-text-muted); }
.status-conflict { color: var(--color-destructive); }

.match-requirement {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.decision-section {
  max-width: 600px;
}

.decision-card {
  padding: var(--spacing-6);
}

.decision-suggestion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-6);
}

.suggestion-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.suggestion-value {
  font-size: var(--font-size-lg);
  font-weight: 700;
}

.suggestion-recommend { color: var(--color-success); }
.suggestion-caution { color: var(--color-warning); }
.suggestion-hold { color: var(--color-info); }
.suggestion-reject { color: var(--color-destructive); }

.decision-reasons {
  margin-bottom: var(--spacing-6);
}

.decision-reasons h4 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  margin-bottom: var(--spacing-2);
}

.decision-reasons ul {
  padding-left: var(--spacing-5);
  color: var(--color-text-secondary);
}

.decision-reasons li {
  margin-bottom: var(--spacing-1);
}

.final-decision {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-4);
  background: var(--color-background);
  border-radius: var(--radius-md);
  margin-bottom: var(--spacing-4);
}

.final-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.final-value {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--color-text-primary);
}

.decision-actions {
  display: flex;
  gap: var(--spacing-3);
  justify-content: center;
}

.report-item {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
  padding: var(--spacing-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  transition: all var(--transition-base);
}

.report-item.report-ready {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent), var(--shadow-md);
}

.report-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.report-info {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.report-version {
  font-weight: 700;
  font-size: var(--font-size-lg);
  color: var(--color-text-primary);
  letter-spacing: -0.01em;
}

.report-date {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.report-actions {
  display: flex;
  gap: var(--spacing-2);
}

.report-error-text {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  font-size: var(--font-size-sm);
  color: var(--color-destructive);
  padding: var(--spacing-3);
  background: linear-gradient(135deg, #fee2e2, #fecaca);
  border-radius: var(--radius-lg);
}

.report-generating {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.report-generating span {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  text-align: center;
}

.empty-state-small {
  padding: var(--spacing-8);
  text-align: center;
  color: var(--color-text-muted);
  background: var(--color-background);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-3);
}

.upload-icon {
  font-size: 40px;
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-2);
}

.upload-link {
  color: var(--color-accent);
}

.upload-tip {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin-top: var(--spacing-2);
}

/* 投标分析 */
.agent-runs-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}

.agent-run-item {
  padding: var(--spacing-4);
  cursor: pointer;
}

.agent-run-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-2);
}

.agent-run-time {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin-left: auto;
}

.agent-run-goal {
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.agent-run-conclusion {
  font-size: var(--font-size-sm);
  color: var(--color-accent);
  font-weight: 500;
}

.run-detail {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.run-detail-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.run-actions {
  display: flex;
  gap: var(--spacing-2);
  margin-top: var(--spacing-2);
}

.run-section {
  margin-top: var(--spacing-5);
}

.run-section-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-3);
}

.strategy-card, .critique-card {
  background: var(--color-background);
  border-radius: var(--radius-lg);
  padding: var(--spacing-4);
}

.strategy-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-2);
}

.strategy-confidence {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.strategy-rationale {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-3);
}

.strategy-actions {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.strategy-action-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  font-size: var(--font-size-sm);
}

.strategy-action-text {
  flex: 1;
  color: var(--color-text-primary);
}

.strategy-action-owner {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.strategy-residual, .open-questions {
  margin-top: var(--spacing-3);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}

.residual-label {
  color: var(--color-text-muted);
}

.critique-card p {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.critique-list {
  margin-top: var(--spacing-2);
  padding-left: var(--spacing-5);
  font-size: var(--font-size-sm);
  color: var(--color-destructive);
}

.recommendation-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}

.recommendation-item {
  background: var(--color-background);
  border-radius: var(--radius-lg);
  padding: var(--spacing-4);
}

.recommendation-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-2);
}

.recommendation-source {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.recommendation-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.recommendation-desc {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-2);
}

.specialist-summary {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-3);
}

.finding-item {
  border-left: 3px solid var(--color-border);
  padding-left: var(--spacing-3);
  margin-bottom: var(--spacing-3);
}

.finding-head {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-1);
}

.finding-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.finding-conclusion, .finding-action {
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
}

.step-attempt {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

@media (max-width: 1200px) {
  .info-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .project-header {
    align-items: stretch;
    flex-direction: column;
    gap: var(--spacing-4);
  }

  .header-actions {
    justify-content: flex-start;
  }

  .info-cards {
    grid-template-columns: 1fr;
  }

  .stages-timeline {
    flex-direction: column;
  }

  .stage-indicator {
    flex-direction: column;
    width: auto;
  }

  .stage-line {
    width: 2px;
    height: 20px;
    margin: var(--spacing-1) 0;
  }

  .matches-summary {
    grid-template-columns: repeat(2, 1fr);
  }
}

/* 决策 Banner */
.decision-banner {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  padding: var(--spacing-4) var(--spacing-6);
  border-radius: var(--radius-xl);
  border: 1px solid;
}

.decision-recommend { background: linear-gradient(135deg, #dcfce7, #bbf7d0); border-color: #22c55e; }
.decision-caution { background: linear-gradient(135deg, #fef3c7, #fde68a); border-color: #f59e0b; }
.decision-hold { background: linear-gradient(135deg, #e0e7ff, #c7d2fe); border-color: #6366f1; }
.decision-reject { background: linear-gradient(135deg, #fee2e2, #fecaca); border-color: #ef4444; }

.decision-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  font-weight: 600;
}

.decision-value {
  font-size: var(--font-size-base);
  font-weight: 700;
  flex: 1;
}

.decision-score {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

/* 投标分析（bid pipeline） */
.bid-upload-section {
  padding: var(--spacing-4) 0;
}

.bid-upload-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-5);
  padding: var(--spacing-6);
}

.bid-upload {
  width: 100%;
  max-width: 600px;
}

.bid-upload :deep(.el-upload-dragger) {
  padding: 40px 20px;
  border-radius: var(--radius-xl);
}

.enterprise-input {
  width: 100%;
  max-width: 400px;
}

.input-label {
  display: block;
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-2);
}

/* 四步业务进度 */
.bid-progress-section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-5);
}

.pipeline-stages {
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  padding: var(--spacing-6);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
}

.next-step-action {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-4);
  margin-top: var(--spacing-4);
  padding: var(--spacing-4) var(--spacing-5);
  border: 1px solid color-mix(in srgb, var(--color-accent) 28%, var(--color-border));
  border-radius: var(--radius-lg);
  background: color-mix(in srgb, var(--color-accent) 6%, var(--color-surface));
}

.next-step-action strong,
.next-step-action span {
  display: block;
}

.next-step-action span {
  margin-top: var(--spacing-1);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.stage-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-3) var(--spacing-4);
  position: relative;
  flex: 1;
  min-width: 100px;
}

.stage-connector {
  position: absolute;
  top: 22px;
  right: -50%;
  width: 100%;
  height: 2px;
  background: var(--color-border);
  z-index: 0;
}

.stage-item.stage-completed .stage-connector {
  background: var(--color-success);
}

.stage-indicator {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface);
  border: 2px solid var(--color-border);
  z-index: 1;
}

.stage-completed .stage-indicator {
  background: var(--color-success);
  border-color: var(--color-success);
  color: white;
}

.stage-processing .stage-indicator {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: white;
}

.stage-failed .stage-indicator {
  background: #ef4444;
  border-color: #ef4444;
  color: white;
}

.stage-check, .stage-spinner, .stage-error {
  display: flex;
  align-items: center;
  justify-content: center;
}

.stage-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-border);
}

.stage-info {
  text-align: center;
}

.stage-name {
  display: block;
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--color-text-primary);
}

.stage-desc {
  display: block;
  font-size: 11px;
  color: var(--color-text-muted);
}

.stage-pending .stage-name,
.stage-pending .stage-desc {
  color: var(--color-text-muted);
}

.evidence-summary {
  margin-bottom: var(--spacing-4);
}

.evidence-list {
  min-height: 96px;
}

.evidence-card {
  margin-bottom: var(--spacing-3);
  padding: var(--spacing-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-background);
}

.evidence-meta {
  margin-bottom: var(--spacing-2);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.evidence-card blockquote {
  margin: 0;
  white-space: pre-wrap;
  line-height: 1.75;
  color: var(--color-text-primary);
}

.report-preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--spacing-4);
  width: 100%;
}

.report-preview-header strong,
.report-preview-header span {
  display: block;
}

.report-preview-header span {
  margin-top: 2px;
  font-size: var(--font-size-xs);
  font-weight: 400;
  color: var(--color-text-muted);
}

.report-preview {
  min-height: 240px;
  max-height: 76vh;
  overflow-y: auto;
  padding-right: var(--spacing-2);
}

.report-preview-section {
  padding: var(--spacing-5) 0;
  border-bottom: 1px solid var(--color-border);
}

.report-preview-section:first-child { padding-top: 0; }
.report-preview-section:last-child { border-bottom: 0; }

.report-preview-section-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--spacing-3);
  margin-bottom: var(--spacing-3);
}

.report-preview-section-title h3 {
  margin: 0;
  font-size: var(--font-size-lg);
}

.report-preview-section-title span {
  flex-shrink: 0;
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

/* 人工复核 */
.human-review-panel {
  padding: var(--spacing-6);
}

.review-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  margin: 0 0 var(--spacing-2);
}

.review-desc {
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-4);
}

.review-items {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
  margin-bottom: var(--spacing-4);
}

.review-item {
  display: grid;
  grid-template-columns: 120px 1fr 100px 200px;
  gap: var(--spacing-3);
  align-items: center;
  padding: var(--spacing-3);
  background: var(--color-surface);
  border-radius: var(--radius-md);
}

.review-tag {
  font-weight: 600;
  font-size: var(--font-size-sm);
  color: var(--color-accent);
}

.review-value {
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
}

.review-conf {
  color: var(--color-text-muted);
  font-size: var(--font-size-xs);
}

/* 报告区 */
.bid-report-section {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-5);
}

.score-cards {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--spacing-4);
}

.score-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: var(--spacing-5);
  text-align: center;
  box-shadow: var(--shadow-sm);
}

.score-value {
  font-size: 32px;
  font-weight: 700;
  line-height: 1.2;
  margin-bottom: var(--spacing-2);
}

.score-value.score-high { color: #22c55e; }
.score-value.score-mid { color: #f59e0b; }
.score-value.score-low { color: #ef4444; }

.score-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-3);
}

.report-content {
  padding: var(--spacing-6);
  background: var(--color-surface);
  border-radius: var(--radius-xl);
}

.report-actions {
  display: flex;
  justify-content: center;
  gap: var(--spacing-4);
}

.doc-card {
  cursor: pointer;
}

.doc-card,
.report-card {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.doc-card .card-body,
.report-card > div:first-child {
  min-width: 0;
  flex: 1;
}

.card-action {
  margin-left: auto;
  flex: none;
}

.analysis-run-section {
  margin: 24px 0;
  padding: 20px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
}

.section-heading, .analysis-actions, .run-summary, .analysis-timeline {
  display: flex;
  align-items: center;
}

.section-heading {
  justify-content: space-between;
  gap: 16px;
}

.section-heading p {
  margin: 4px 0 0;
  color: var(--color-text-secondary);
}

.analysis-actions, .run-summary {
  gap: 12px;
}

.analysis-run-card {
  margin-top: 16px;
}

.run-summary {
  flex-wrap: wrap;
  margin-bottom: 16px;
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.analysis-timeline {
  justify-content: space-between;
  gap: 8px;
}

.analysis-stage {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
}

.analysis-stage-completed { color: #16a34a; }
.analysis-stage-processing { color: #2563eb; }
.analysis-stage-failed { color: #dc2626; }

.snapshot-timeline { margin: 24px 8px 0; }
.snapshot-output { display: block; margin-top: 4px; color: var(--color-text-secondary); }

@media (max-width: 720px) {
  .section-heading, .analysis-timeline { align-items: flex-start; flex-direction: column; }
  .analysis-actions { width: 100%; }
}
</style>
