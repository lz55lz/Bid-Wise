<template>
  <div class="project-list">
    <div class="page-header">
      <div>
        <h1 class="page-title">项目管理</h1>
        <p class="page-subtitle">管理和跟踪所有投标项目</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="handleCreate">
        新建项目
      </el-button>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索项目名称或编号"
        :prefix-icon="Search"
        clearable
        style="width: 280px"
        @change="handleSearch"
      />

      <el-select v-model="statusFilter" placeholder="项目状态" clearable style="width: 160px" @change="handleSearch">
        <el-option label="全部" value="" />
        <el-option label="草稿" value="DRAFT" />
        <el-option label="进行中" value="ACTIVE" />
        <el-option label="已归档" value="ARCHIVED" />
      </el-select>

      <el-select v-model="typeFilter" placeholder="项目类型" clearable style="width: 160px" @change="handleSearch">
        <el-option label="全部类型" value="" />
        <el-option label="工程建设" value="ENGINEERING" />
        <el-option label="政府采购" value="GOVERNMENT" />
        <el-option label="企业采购" value="ENTERPRISE" />
        <el-option label="其他" value="OTHER" />
      </el-select>
    </div>

    <!-- 项目列表 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中...</span>
    </div>

    <div v-else-if="projects.length" class="projects-table">
      <el-table :data="projects" stripe @row-click="handleRowClick">
        <el-table-column prop="code" label="项目编号" width="160">
          <template #default="{ row }">
            <span class="project-code">{{ row.code }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="项目名称" min-width="200">
          <template #default="{ row }">
            <div class="project-name-cell">
              <span class="project-name">{{ row.name }}</span>
              <span class="project-purchaser">{{ row.purchaser }}</span>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="project_type" label="类型" width="120">
          <template #default="{ row }">
            <span class="type-tag">{{ getTypeText(row.project_type) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="region" label="地区" width="120" />

        <el-table-column prop="bid_deadline" label="截止时间" width="140">
          <template #default="{ row }">
            <span class="deadline-text" :class="{ overdue: isOverdue(row.bid_deadline) }">
              {{ formatDate(row.bid_deadline) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <span :class="['badge', `badge-${getStatusClass(row.status)}`]">
              {{ getStatusText(row.status) }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="当前阶段" width="160">
          <template #default="{ row }">
            <span class="project-phase">{{ getProjectPhase(row) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button text size="small" @click.stop="router.push(`/projects/${row.id}`)">
                查看
              </el-button>
              <template v-if="row.status !== 'ARCHIVED'">
                <el-button text size="small" type="warning" @click.stop="handleAction('archive', row)">
                  归档
                </el-button>
                <el-button text size="small" type="danger" @click.stop="handleAction('delete', row)">
                  删除
                </el-button>
              </template>
              <el-tag v-else type="info" size="small">已归档</el-tag>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><FolderOpened /></el-icon>
      <h3 class="empty-state-title">暂无项目</h3>
      <p class="empty-state-description">创建您的第一个投标项目，开始智能分析</p>
      <el-button type="primary" @click="handleCreate">新建项目</el-button>
    </div>

    <!-- 分页 -->
    <div v-if="total > 0" class="pagination">
      <el-pagination
        v-model:current-page="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50]"
        layout="total, sizes, prev, pager, next"
        @size-change="handleSizeChange"
        @current-change="handlePageChange"
      />
    </div>

    <!-- 创建项目对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="新建项目"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-alert v-if="pendingTenderFile" type="info" :closable="false" show-icon class="pending-file-alert">
        将在创建项目后上传并解析：{{ pendingTenderFile.name }}
      </el-alert>
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="100px">
        <el-form-item label="项目名称" prop="name">
          <el-input v-model="createForm.name" placeholder="请输入项目名称" />
        </el-form-item>


        <el-form-item label="招标人" prop="purchaser">
          <el-input v-model="createForm.purchaser" placeholder="请输入招标人名称" />
        </el-form-item>

        <el-form-item label="项目类型" prop="project_type">
          <el-select v-model="createForm.project_type" placeholder="请选择项目类型" style="width: 100%">
            <el-option label="工程建设" value="ENGINEERING" />
            <el-option label="政府采购" value="GOVERNMENT" />
            <el-option label="企业采购" value="ENTERPRISE" />
            <el-option label="其他" value="OTHER" />
          </el-select>
        </el-form-item>

        <el-form-item label="地区" prop="region">
          <el-input v-model="createForm.region" placeholder="请输入项目所在地区" />
        </el-form-item>

        <el-form-item label="投标截止" prop="deadline">
          <el-date-picker v-model="createForm.deadline" type="datetime" placeholder="选择投标截止时间" style="width: 100%" />
        </el-form-item>

        <el-form-item label="投标企业" prop="enterprise_ids">
          <el-select
            v-model="createForm.enterprise_ids"
            multiple
            placeholder="请选择投标企业(联合体),首个为主投标人"
            style="width: 100%"
          >
            <el-option
              v-for="e in enterprises"
              :key="e.id"
              :label="e.name"
              :value="e.id"
            />
          </el-select>
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreateSubmit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores'
import { documentApi, enterpriseApi, reportApi } from '@/api'
import { usePendingTenderUpload } from '@/composables/usePendingTenderUpload'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Search, FolderOpened, Loading } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance, FormRules } from 'element-plus'
import type { Project, Enterprise } from '@/types'

const router = useRouter()
const route = useRoute()
const projectStore = useProjectStore()
const { pendingTenderFile, consumePendingTenderFile } = usePendingTenderUpload()

const loading = ref(false)
const projects = ref<Project[]>([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)

const searchKeyword = ref('')
const statusFilter = ref('')
const typeFilter = ref('')

const showCreateDialog = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()
const enterprises = ref<Enterprise[]>([])
const projectPhases = ref<Record<string, string>>({})

const fetchEnterprises = async () => {
  try {
    enterprises.value = await enterpriseApi.list()
  } catch {
    // 企业列表加载失败时,新建表单的企业下拉为空,提交会被后端 422 拦截
  }
}

const createForm = reactive({
  name: '',
  purchaser: '',
  project_type: '',
  region: '',
  deadline: null as Date | null,
  enterprise_ids: [] as string[],
})

const createRules: FormRules = {
  name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
  purchaser: [{ required: true, message: '请输入招标人', trigger: 'blur' }],
  project_type: [{ required: true, message: '请选择项目类型', trigger: 'change' }],
  region: [{ required: true, message: '请输入地区', trigger: 'blur' }],
  deadline: [{ required: true, message: '请选择投标截止时间', trigger: 'change' }],
  enterprise_ids: [{ required: true, type: 'array', min: 1, message: '请选择至少一家投标企业', trigger: 'change' }],
}

const fetchProjects = async () => {
  loading.value = true
  try {
    const params = {
      status: statusFilter.value || undefined,
    }
    const response = await projectStore.fetchProjects(params)
    if (Array.isArray(response)) {
      // 前端过滤
      let filtered = response
      if (typeFilter.value) {
        filtered = filtered.filter(p => p.project_type === typeFilter.value)
      }
      if (searchKeyword.value) {
        const kw = searchKeyword.value.toLowerCase()
        filtered = filtered.filter(p =>
          p.name.toLowerCase().includes(kw) || p.code.toLowerCase().includes(kw)
        )
      }
      projects.value = filtered
      total.value = filtered.length
      const phaseResults = await Promise.allSettled(
        filtered.map(async (project) => ({
          projectId: project.id,
          report: await reportApi.latest(project.id),
        })),
      )
      const phases: Record<string, string> = {}
      for (const result of phaseResults) {
        if (result.status !== 'fulfilled') continue
        const { projectId, report } = result.value
        phases[projectId] = report?.status === 'READY'
          ? '报告已生成'
          : report?.status === 'GENERATING'
            ? '报告生成中'
            : getBaseProjectPhase(filtered.find(project => project.id === projectId)?.status)
      }
      projectPhases.value = phases
    }
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  currentPage.value = 1
  fetchProjects()
}

const handleSizeChange = () => {
  currentPage.value = 1
  fetchProjects()
}

const handlePageChange = () => {
  fetchProjects()
}

const handleCreate = () => {
  showCreateDialog.value = true
}

const handleCreateSubmit = async () => {
  if (!createFormRef.value) return

  try {
    await createFormRef.value.validate()
    creating.value = true

    const payload = { ...createForm } as any
    delete payload.deadline
    delete payload.status
    const project = await projectStore.createProject({
      ...payload,
      bid_deadline: createForm.deadline?.toISOString(),
    } as Partial<Project>)

    const tenderFile = consumePendingTenderFile()
    if (tenderFile) {
      try {
        await documentApi.upload(project.id, tenderFile, 'TENDER')
        ElMessage.success('项目已创建，招标文件正在解析')
      } catch (error: any) {
        ElMessage.error(error?.message || '项目已创建，但文件上传失败，请在项目详情页重新上传')
      }
    } else {
      ElMessage.success('项目创建成功')
    }
    showCreateDialog.value = false
    router.push(`/projects/${project.id}`)
  } catch (error) {
    // validation failed
  } finally {
    creating.value = false
  }
}

const handleRowClick = (row: Project) => {
  router.push(`/projects/${row.id}`)
}

const handleAction = async (command: string, row: any) => {
  if (command === 'archive') {
    try {
      await ElMessageBox.confirm('确定要归档该项目吗？归档后项目将只读。', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      })
      await projectStore.archiveProject(row.id)
      ElMessage.success('项目已归档')
      fetchProjects()
    } catch {
      // cancel
    }
  } else if (command === 'delete') {
    try {
      await ElMessageBox.confirm('确定要删除该项目吗？删除后不可恢复。', '警告', {
        confirmButtonText: '删除',
        cancelButtonText: '取消',
        type: 'warning',
      })
      await projectStore.deleteProject(row.id)
      ElMessage.success('项目已删除')
      fetchProjects()
    } catch {
      // cancel
    }
  } else {
    ElMessage.warning('未知操作')
  }
}

const getTypeText = (type: string) => {
  const map: Record<string, string> = {
    ENGINEERING: '工程建设',
    GOVERNMENT: '政府采购',
    ENTERPRISE: '企业采购',
    OTHER: '其他',
  }
  return map[type] || type
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = {
    DRAFT: '草稿',
    ACTIVE: '进行中',
    ARCHIVED: '已归档',
  }
  return map[status] || status
}

const getStatusClass = (status: string) => {
  const map: Record<string, string> = {
    DRAFT: 'draft',
    ACTIVE: 'active',
    ARCHIVED: 'pending',
  }
  return map[status] || 'pending'
}

const formatDate = (date: string) => {
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

const isOverdue = (deadline: string) => {
  return dayjs(deadline).isBefore(dayjs())
}

const getBaseProjectPhase = (status?: string) => ({
  DRAFT: '待上传招标文件',
  ACTIVE: '投标准备中',
  ARCHIVED: '项目已归档',
} as Record<string, string>)[status || ''] || '状态待更新'

const getProjectPhase = (project: any) => projectPhases.value[project.id] || getBaseProjectPhase(project.status)

onMounted(() => {
  fetchProjects()
  fetchEnterprises()
  if (route.query.create === 'tender' && pendingTenderFile.value) {
    showCreateDialog.value = true
    router.replace({ path: '/projects' })
  }
})
</script>

<style scoped>
.project-list {
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

.pending-file-alert {
  margin-bottom: var(--spacing-4);
}

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-2);
  padding: var(--spacing-16);
  color: var(--color-text-muted);
}

.projects-table {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

.project-code {
  font-family: monospace;
  color: var(--color-text-secondary);
}

.project-name-cell {
  display: flex;
  flex-direction: column;
}

.project-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.project-purchaser {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.type-tag {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: var(--color-background);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}

.deadline-text {
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
}

.deadline-text.overdue {
  color: var(--color-destructive);
}

.project-phase {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.action-buttons {
  display: flex;
  gap: var(--spacing-1);
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--spacing-4);
}
</style>
