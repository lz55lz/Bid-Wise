<template>
  <div class="knowledge-base">
    <div class="page-header">
      <div>
        <h1 class="page-title">知识库管理</h1>
        <p class="page-subtitle">管理法规与案例知识，发布后可用于智能问答</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Upload" @click="openImportDialog">从文档导入</el-button>
        <el-button type="primary" :icon="Plus" @click="openCreateDialog">新建知识</el-button>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input v-model="searchKeyword" placeholder="搜索标题 / 来源 / 内容" :prefix-icon="Search" clearable style="width: 260px" @change="fetchEntries" />

      <el-select v-model="typeFilter" placeholder="知识类型" clearable style="width: 140px">
        <el-option label="全部类型" value="" />
        <el-option label="法规" value="LEGAL" />
        <el-option label="案例" value="CASE" />
      </el-select>

      <el-select v-model="statusFilter" placeholder="状态" clearable style="width: 140px">
        <el-option label="全部状态" value="" />
        <el-option label="草稿" value="DRAFT" />
        <el-option label="已发布" value="PUBLISHED" />
        <el-option label="已归档" value="ARCHIVED" />
      </el-select>
    </div>

    <!-- 知识列表 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中...</span>
    </div>

    <div v-else-if="filteredEntries.length" class="entries-table">
      <el-table :data="filteredEntries" stripe>
        <el-table-column label="标题" min-width="220">
          <template #default="{ row }">
            <div class="entry-title">
              <span class="name">{{ row.title }}</span>
              <span class="version-tag">v{{ row.version_no }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <span :class="['type-badge', `type-${row.knowledge_type.toLowerCase()}`]">
              {{ getTypeText(row.knowledge_type) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column prop="source_reference" label="来源" min-width="160" show-overflow-tooltip />

        <el-table-column label="发布机关" width="140">
          <template #default="{ row }">
            <span>{{ row.authority || '-' }}</span>
          </template>
        </el-table-column>

        <el-table-column label="发布/生效日期" width="200">
          <template #default="{ row }">
            <span>{{ formatDate(row.issued_on) }} / {{ formatDate(row.effective_on) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="140">
          <template #default="{ row }">
            <div class="status-cell">
              <span :class="['status-badge', row.status.toLowerCase()]">
                {{ getStatusText(row.status) }}
              </span>
              <div v-if="getParseProgress(row as KnowledgeEntry) > 0" class="parse-progress">
                <el-progress
                  :percentage="getParseProgress(row as KnowledgeEntry)"
                  :status="getParseStatus(row.source_parse_status)"
                  :show-info="false"
                  :stroke-width="4"
                />
                <span class="parse-label">{{ getParseText(row.source_parse_status) }}</span>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button text size="small" @click="openDetailDialog(row as KnowledgeEntry)">查看</el-button>
              <el-button text size="small" @click="openReviseDialog(row as KnowledgeEntry)">编辑</el-button>
              <el-button
                v-if="row.status !== 'PUBLISHED'"
                text size="small" type="success"
                @click="handlePublish(row as KnowledgeEntry)"
              >发布</el-button>
              <el-button
                v-else
                text size="small" type="warning"
                @click="handleUnpublish(row as KnowledgeEntry)"
              >下架</el-button>
              <el-button text size="small" type="danger" @click="handleDelete(row as KnowledgeEntry)">删除</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><Reading /></el-icon>
      <h3 class="empty-state-title">暂无知识条目</h3>
      <p class="empty-state-description">新建法规/案例知识，或从文档导入，发布后可用于智能问答</p>
      <el-button type="primary" @click="openCreateDialog">新建知识</el-button>
    </div>

    <!-- 新建知识对话框 -->
    <el-dialog v-model="showCreateDialog" title="新建知识" width="640px" :close-on-click-modal="false">
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="100px">
        <el-form-item label="知识类型" prop="knowledge_type">
          <el-radio-group v-model="createForm.knowledge_type">
            <el-radio-button value="LEGAL">法规</el-radio-button>
            <el-radio-button value="CASE">案例</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="标题" prop="title">
          <el-input v-model="createForm.title" placeholder="如：《招标投标法实施条例》第三条" />
        </el-form-item>

        <el-form-item label="来源" prop="source_reference">
          <el-input v-model="createForm.source_reference" placeholder="如：国务院令第613号 / 判决书文号" />
        </el-form-item>

        <el-form-item label="发布机关">
          <el-input v-model="createForm.authority" placeholder="请输入发布机关（可选）" />
        </el-form-item>

        <el-form-item label="日期">
          <div class="date-row">
            <el-date-picker v-model="createForm.issued_on" type="date" placeholder="发布日期" value-format="YYYY-MM-DD" />
            <el-date-picker v-model="createForm.effective_on" type="date" placeholder="生效日期" value-format="YYYY-MM-DD" />
          </div>
        </el-form-item>

        <el-form-item label="内容" prop="content">
          <el-input v-model="createForm.content" type="textarea" :rows="8" placeholder="请输入知识正文，将用于智能问答检索" />
        </el-form-item>

        <el-form-item label="引用说明">
          <el-input v-model="createForm.citation_note" type="textarea" :rows="2" placeholder="引用时的备注说明（可选）" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleCreateSubmit">创建</el-button>
      </template>
    </el-dialog>

    <!-- 编辑（修订）对话框 -->
    <el-dialog v-model="showReviseDialog" title="编辑知识" width="640px" :close-on-click-modal="false">
      <el-alert type="info" :closable="false" class="revise-tip">
        保存后将生成新版本（v{{ (currentEntry?.version_no ?? 0) + 1 }}），状态回到草稿，需重新发布后才会用于问答。
      </el-alert>
      <el-form ref="reviseFormRef" :model="reviseForm" :rules="reviseRules" label-width="100px">
        <el-form-item label="标题">
          <el-input :model-value="currentEntry?.title" disabled />
        </el-form-item>

        <el-form-item label="内容" prop="content">
          <el-input v-model="reviseForm.content" type="textarea" :rows="10" />
        </el-form-item>

        <el-form-item label="日期">
          <div class="date-row">
            <el-date-picker v-model="reviseForm.issued_on" type="date" placeholder="发布日期" value-format="YYYY-MM-DD" />
            <el-date-picker v-model="reviseForm.effective_on" type="date" placeholder="生效日期" value-format="YYYY-MM-DD" />
          </div>
        </el-form-item>

        <el-form-item label="引用说明">
          <el-input v-model="reviseForm.citation_note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showReviseDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleReviseSubmit">保存新版本</el-button>
      </template>
    </el-dialog>

    <!-- 从文档导入对话框 -->
    <el-dialog v-model="showImportDialog" title="从文档导入" width="560px" :close-on-click-modal="false">
      <el-alert v-if="importForm.file" type="info" :closable="false" show-icon class="pending-import-file">
        将导入并解析：{{ importForm.file.name }}
      </el-alert>
      <el-form ref="importFormRef" :model="importForm" :rules="importRules" label-width="100px">
        <el-form-item label="文档" prop="file">
          <div v-if="importForm.file" class="selected-import-file">
            <span>{{ importForm.file.name }}</span>
            <el-tag size="small" type="success">已选择</el-tag>
          </div>
          <el-upload
            v-else
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".pdf,.docx,.xlsx,.pptx,.jpg,.jpeg,.png"
            drag
            :on-change="(f: any) => (importForm.file = f.raw)"
            :on-remove="() => (importForm.file = null)"
          >
            <el-icon class="upload-drag-icon"><UploadFilled /></el-icon>
            <span>将文件拖到此处，或<span class="upload-link">点击上传</span></span>
            <template #tip>
              <div class="upload-tip">支持 PDF、DOCX、XLSX、PPTX、JPG、PNG；解析完成后请审核并发布</div>
            </template>
          </el-upload>
        </el-form-item>

        <el-form-item label="知识类型" prop="knowledge_type">
          <el-radio-group v-model="importForm.knowledge_type">
            <el-radio-button value="LEGAL">法规</el-radio-button>
            <el-radio-button value="CASE">案例</el-radio-button>
          </el-radio-group>
        </el-form-item>

        <el-form-item label="标题" prop="title">
          <el-input v-model="importForm.title" placeholder="请输入知识标题" />
        </el-form-item>

        <el-form-item label="来源" prop="source_reference">
          <el-input v-model="importForm.source_reference" placeholder="如：国务院令第613号" />
        </el-form-item>

        <el-form-item label="发布机关">
          <el-input v-model="importForm.authority" placeholder="请输入发布机关（可选）" />
        </el-form-item>

        <el-form-item label="日期">
          <div class="date-row">
            <el-date-picker v-model="importForm.issued_on" type="date" placeholder="发布日期" value-format="YYYY-MM-DD" />
            <el-date-picker v-model="importForm.effective_on" type="date" placeholder="生效日期" value-format="YYYY-MM-DD" />
          </div>
        </el-form-item>

        <el-form-item label="引用说明">
          <el-input v-model="importForm.citation_note" type="textarea" :rows="2" placeholder="引用时的备注说明（可选）" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showImportDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="handleImportSubmit">上传并解析</el-button>
      </template>
    </el-dialog>

    <!-- 详情对话框 -->
    <el-dialog v-model="showDetailDialog" :title="currentEntry?.title" width="680px">
      <div v-if="currentEntry" class="detail-body">
        <div class="detail-meta">
          <span :class="['type-badge', `type-${currentEntry.knowledge_type.toLowerCase()}`]">
            {{ getTypeText(currentEntry.knowledge_type) }}
          </span>
          <span :class="['status-badge', currentEntry.status.toLowerCase()]">
            {{ getStatusText(currentEntry.status) }}
          </span>
          <span class="meta-item">版本 v{{ currentEntry.version_no }}</span>
          <span class="meta-item">来源：{{ currentEntry.source_reference }}</span>
          <span v-if="currentEntry.authority" class="meta-item">发布机关：{{ currentEntry.authority }}</span>
          <span class="meta-item">发布：{{ formatDate(currentEntry.issued_on) }}</span>
          <span class="meta-item">生效：{{ formatDate(currentEntry.effective_on) }}</span>
        </div>
        <div class="detail-content">{{ currentEntry.content }}</div>
        <div v-if="currentEntry.citation_note" class="detail-note">
          引用说明：{{ currentEntry.citation_note }}
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, Loading, Reading, Upload, UploadFilled } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance, FormRules } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { knowledgeApi } from '@/api'
import { usePendingTenderUpload } from '@/composables/usePendingTenderUpload'
import type { KnowledgeEntry } from '@/types'

const loading = ref(false)
const route = useRoute()
const router = useRouter()
const { pendingLegalFile, consumePendingLegalFile } = usePendingTenderUpload()
const entries = ref<KnowledgeEntry[]>([])
const searchKeyword = ref('')
const typeFilter = ref('')
const statusFilter = ref('')

const submitting = ref(false)
const currentEntry = ref<KnowledgeEntry | null>(null)

// 新建
const showCreateDialog = ref(false)
const createFormRef = ref<FormInstance>()
const createForm = reactive({
  knowledge_type: 'LEGAL',
  title: '',
  source_reference: '',
  authority: '',
  issued_on: null as string | null,
  effective_on: null as string | null,
  content: '',
  citation_note: '',
})

const createRules: FormRules = {
  knowledge_type: [{ required: true, message: '请选择知识类型', trigger: 'change' }],
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
  source_reference: [{ required: true, message: '请输入来源', trigger: 'blur' }],
  content: [{ required: true, message: '请输入内容', trigger: 'blur' }],
}

// 编辑（修订）
const showReviseDialog = ref(false)
const reviseFormRef = ref<FormInstance>()
const reviseForm = reactive({
  content: '',
  issued_on: null as string | null,
  effective_on: null as string | null,
  citation_note: '',
})

const reviseRules: FormRules = {
  content: [{ required: true, message: '请输入内容', trigger: 'blur' }],
}

// 文档导入
const showImportDialog = ref(false)
const importFormRef = ref<FormInstance>()
const importForm = reactive({
  file: null as File | null,
  knowledge_type: 'LEGAL',
  title: '',
  source_reference: '',
  authority: '',
  issued_on: null as string | null,
  effective_on: null as string | null,
  citation_note: '',
})

const importRules: FormRules = {
  file: [{ required: true, message: '请选择文件', trigger: 'change' }],
  knowledge_type: [{ required: true, message: '请选择知识类型', trigger: 'change' }],
  title: [{ required: true, message: '请输入标题', trigger: 'blur' }],
  source_reference: [{ required: true, message: '请输入来源', trigger: 'blur' }],
}

// 详情
const showDetailDialog = ref(false)

// 类型和状态为客户端筛选，搜索走后端 query
const filteredEntries = computed(() =>
  entries.value.filter(e =>
    (!typeFilter.value || e.knowledge_type === typeFilter.value) &&
    (!statusFilter.value || e.status === statusFilter.value)
  )
)

const fetchEntries = async () => {
  loading.value = true
  try {
    entries.value = await knowledgeApi.list(searchKeyword.value || undefined)
  } finally {
    loading.value = false
  }
}

const openCreateDialog = () => {
  createForm.knowledge_type = 'LEGAL'
  createForm.title = ''
  createForm.source_reference = ''
  createForm.authority = ''
  createForm.issued_on = null
  createForm.effective_on = null
  createForm.content = ''
  createForm.citation_note = ''
  showCreateDialog.value = true
}

const handleCreateSubmit = async () => {
  if (!createFormRef.value) return
  try {
    await createFormRef.value.validate()
    submitting.value = true
    await knowledgeApi.create({
      knowledge_type: createForm.knowledge_type,
      title: createForm.title,
      source_reference: createForm.source_reference,
      authority: createForm.authority || undefined,
      issued_on: createForm.issued_on || undefined,
      effective_on: createForm.effective_on || undefined,
      content: createForm.content,
      citation_note: createForm.citation_note || undefined,
    })
    ElMessage.success('知识已创建（草稿），发布后可用于问答')
    showCreateDialog.value = false
    fetchEntries()
  } catch (e: any) {
    if (e?.message) ElMessage.error(e.message)
  } finally {
    submitting.value = false
  }
}

const openReviseDialog = (entry: KnowledgeEntry) => {
  currentEntry.value = entry
  reviseForm.content = entry.content
  reviseForm.issued_on = entry.issued_on
  reviseForm.effective_on = entry.effective_on
  reviseForm.citation_note = entry.citation_note || ''
  showReviseDialog.value = true
}

const handleReviseSubmit = async () => {
  if (!reviseFormRef.value || !currentEntry.value) return
  try {
    await reviseFormRef.value.validate()
    submitting.value = true
    await knowledgeApi.revise(currentEntry.value.entry_id, {
      content: reviseForm.content,
      issued_on: reviseForm.issued_on,
      effective_on: reviseForm.effective_on,
      citation_note: reviseForm.citation_note || null,
    })
    ElMessage.success('已保存为新版本（草稿），发布后生效')
    showReviseDialog.value = false
    fetchEntries()
  } catch (e: any) {
    if (e?.message) ElMessage.error(e.message)
  } finally {
    submitting.value = false
  }
}

const openImportDialog = () => {
  importForm.file = null
  importForm.knowledge_type = 'LEGAL'
  importForm.title = ''
  importForm.source_reference = ''
  importForm.authority = ''
  importForm.issued_on = null
  importForm.effective_on = null
  importForm.citation_note = ''
  showImportDialog.value = true
}

const handleImportSubmit = async () => {
  if (!importFormRef.value) return
  try {
    await importFormRef.value.validate()
    if (!importForm.file) return
    submitting.value = true
    await knowledgeApi.uploadDocument('', importForm.file, importForm.knowledge_type, {
      title: importForm.title,
      source_reference: importForm.source_reference,
      authority: importForm.authority || undefined,
      issued_on: importForm.issued_on || undefined,
      effective_on: importForm.effective_on || undefined,
      citation_note: importForm.citation_note || undefined,
    })
    ElMessage.success('文档已上传，正在解析，稍后请刷新列表')
    showImportDialog.value = false
    fetchEntries()
  } catch (e: any) {
    if (e?.message) ElMessage.error(e.message)
  } finally {
    submitting.value = false
  }
}

const openDetailDialog = (entry: KnowledgeEntry) => {
  currentEntry.value = entry
  showDetailDialog.value = true
}

const handlePublish = async (entry: KnowledgeEntry) => {
  try {
    await knowledgeApi.publish(entry.version_id)
    ElMessage.success(`「${entry.title}」已发布`)
    fetchEntries()
  } catch (e: any) {
    ElMessage.error(e?.message || '发布失败')
  }
}

const handleUnpublish = async (entry: KnowledgeEntry) => {
  try {
    await knowledgeApi.unpublish(entry.version_id)
    ElMessage.success(`「${entry.title}」已下架`)
    fetchEntries()
  } catch (e: any) {
    ElMessage.error(e?.message || '下架失败')
  }
}

const handleDelete = async (entry: KnowledgeEntry) => {
  try {
    await ElMessageBox.confirm(`确定删除知识「${entry.title}」吗？删除后不可恢复。`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await knowledgeApi.delete(entry.entry_id)
    ElMessage.success('知识已删除')
    fetchEntries()
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e?.message || '删除失败')
  }
}

const getTypeText = (type: string) => ({ LEGAL: '法规', CASE: '案例' }[type] || type)
const getStatusText = (status: string) => ({ DRAFT: '草稿', PUBLISHED: '已发布', ARCHIVED: '已归档' }[status] || status)
const formatDate = (d?: string | null) => (d ? dayjs(d).format('YYYY-MM-DD') : '-')

const PARSE_PROGRESS: Record<string, number> = {
  UPLOADED: 10,
  QUEUED: 20,
  PARSING: 40,
  CLEANING: 60,
  PARSED: 70,
  STRUCTURING: 80,
  INDEXING: 90,
  READY: 100,
  SUCCESS: 100,
  FAILED: 100,
}

const getParseProgress = (row: KnowledgeEntry) => {
  if (!row.source_parse_status) return 0
  return PARSE_PROGRESS[row.source_parse_status] ?? 0
}

const getParseStatus = (parseStatus?: string) => {
  if (parseStatus === 'FAILED') return 'exception'
  if (parseStatus === 'READY' || parseStatus === 'SUCCESS') return 'success'
  return 'warning'
}

const getParseText = (parseStatus?: string) => ({
  UPLOADED: '待处理',
  QUEUED: '排队中',
  PARSING: '解析中',
  CLEANING: '清洗中',
  PARSED: '已解析',
  STRUCTURING: '结构化',
  INDEXING: '索引中',
  READY: '就绪',
  SUCCESS: '完成',
  FAILED: '失败',
}[parseStatus || ''] || parseStatus || '')

let refreshTimer: ReturnType<typeof setInterval> | null = null

const startAutoRefresh = () => {
  if (refreshTimer) return
  refreshTimer = setInterval(() => {
    const hasProcessing = entries.value.some(e =>
      e.source_parse_status &&
      e.source_parse_status !== 'READY' &&
      e.source_parse_status !== 'SUCCESS' &&
      e.source_parse_status !== 'FAILED'
    )
    if (hasProcessing) {
      fetchEntries()
    } else {
      stopAutoRefresh()
    }
  }, 3000)
}

const stopAutoRefresh = () => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
}

onMounted(() => {
  fetchEntries()
  startAutoRefresh()
  if (route.query.import === 'legal' && pendingLegalFile.value) {
    openImportDialog()
    const file = consumePendingLegalFile()
    if (file) {
      importForm.file = file
      importForm.title = file.name.replace(/\.[^.]+$/, '')
      importForm.source_reference = file.name
    }
    router.replace({ path: '/knowledge' })
  }
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<style scoped>
.knowledge-base {
  max-width: 1400px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-6);
}

.header-actions {
  display: flex;
  gap: var(--spacing-2);
}

.filter-bar {
  display: flex;
  gap: var(--spacing-3);
  margin-bottom: var(--spacing-4);
  padding: var(--spacing-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
}

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-2);
  padding: var(--spacing-16);
  color: var(--color-text-muted);
}

.entries-table {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

.entry-title {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-1);
}

.entry-title .name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.version-tag {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.type-badge {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-sm);
}

.type-legal { background: #E0E7FF; color: #3730A3; }
.type-case { background: #FEF3C7; color: #92400E; }

.status-badge {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-full);
}

.status-badge.draft { background: #F3F4F6; color: #4B5563; }
.status-badge.published { background: #D1FAE5; color: #065F46; }
.status-badge.archived { background: #FEE2E2; color: #991B1B; }

.parse-tag {
  display: block;
  margin-top: var(--spacing-1);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.status-cell {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.parse-progress {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.parse-progress .el-progress {
  width: 100%;
}

.parse-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.action-buttons {
  display: flex;
  gap: var(--spacing-1);
}

.date-row {
  display: flex;
  gap: var(--spacing-2);
  width: 100%;
}

.revise-tip {
  margin-bottom: var(--spacing-4);
}

.pending-import-file {
  margin-bottom: var(--spacing-4);
}

.selected-import-file {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-height: 36px;
  padding: 0 var(--spacing-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-secondary);
}

.upload-drag-icon {
  font-size: 32px;
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-2);
}

.upload-link {
  color: var(--color-accent);
}

.detail-body {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.detail-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--spacing-2);
}

.meta-item {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.detail-content {
  white-space: pre-wrap;
  line-height: 1.7;
  color: var(--color-text-primary);
  max-height: 400px;
  overflow-y: auto;
  padding: var(--spacing-4);
  background: var(--color-background);
  border-radius: var(--radius-lg);
}

.detail-note {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}
</style>
