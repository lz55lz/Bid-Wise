<template>
  <div class="dashboard">
    <!-- 欢迎区域 -->
    <div class="welcome-section">
      <div class="welcome-content">
        <h1 class="welcome-title">{{ greeting }}，{{ user?.username || '用户' }}</h1>
        <p class="welcome-subtitle">这是您的投标工作台，快速了解项目状态</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="handleCreateProject">
        新建项目
      </el-button>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon stat-icon-primary">
          <el-icon><FolderOpened /></el-icon>
        </div>
        <div class="stat-content">
          <span class="stat-value">{{ stats.activeProjects }}</span>
          <span class="stat-label">进行中项目</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon stat-icon-warning">
          <el-icon><Warning /></el-icon>
        </div>
        <div class="stat-content">
          <span class="stat-value">{{ stats.pendingRisks }}</span>
          <span class="stat-label">待处理风险</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon stat-icon-success">
          <el-icon><CircleCheck /></el-icon>
        </div>
        <div class="stat-content">
          <span class="stat-value">{{ stats.completedReports }}</span>
          <span class="stat-label">已完成报告</span>
        </div>
      </div>

      <div class="stat-card">
        <div class="stat-icon stat-icon-info">
          <el-icon><Document /></el-icon>
        </div>
        <div class="stat-content">
          <span class="stat-value">{{ stats.documents }}</span>
          <span class="stat-label">已解析文档</span>
        </div>
      </div>
    </div>

    <!-- 最近项目 -->
    <div class="section">
      <div class="section-header">
        <h2 class="section-title">最近项目</h2>
        <el-button text :icon="ArrowRight" @click="router.push('/projects')">
          查看全部
        </el-button>
      </div>

      <div v-if="recentProjects.length" class="projects-grid">
        <div
          v-for="project in recentProjects"
          :key="project.id"
          class="project-card card"
          @click="router.push(`/projects/${project.id}`)"
        >
          <div class="project-card-header">
            <div class="project-status">
              <span :class="['status-dot', `status-dot-${getStatusDot(project.status)}`]" />
              <span :class="['badge', `badge-${getStatusClass(project.status)}`]">
                {{ getStatusText(project.status) }}
              </span>
            </div>
            <span class="project-code">{{ project.code }}</span>
          </div>

          <h3 class="project-name">{{ project.name }}</h3>
          <p class="project-purchaser">{{ project.purchaser }}</p>

          <div class="project-meta">
            <div class="project-meta-item">
              <el-icon><Calendar /></el-icon>
              <span>截止 {{ formatDate(project.bid_deadline) }}</span>
            </div>
            <div class="project-meta-item">
              <el-icon><Location /></el-icon>
              <span>{{ project.region }}</span>
            </div>
          </div>

          <div class="project-progress">
            <div class="progress-header">
              <span class="progress-label">当前阶段</span>
              <span class="progress-value">{{ getProjectPhase(project) }}</span>
            </div>
          </div>
        </div>
      </div>

      <div v-else class="empty-state">
        <el-icon class="empty-state-icon"><FolderOpened /></el-icon>
        <h3 class="empty-state-title">暂无项目</h3>
        <p class="empty-state-description">创建您的第一个投标项目，开始智能分析</p>
        <el-button type="primary" @click="handleCreateProject">新建项目</el-button>
      </div>
    </div>

    <!-- 待办事项 -->
    <div class="section">
      <div class="section-header">
        <h2 class="section-title">待办事项</h2>
      </div>

      <div v-if="pendingTasks.length" class="tasks-list">
        <div
          v-for="task in pendingTasks"
          :key="task.id"
          class="task-item card"
        >
          <div class="task-icon" :class="`task-icon-${task.type}`">
            <el-icon>
              <component :is="getTaskIcon(task.type)" />
            </el-icon>
          </div>
          <div class="task-content">
            <h4 class="task-title">{{ task.title }}</h4>
            <p class="task-description">{{ task.description }}</p>
          </div>
          <div class="task-action">
            <el-button size="small" @click="handleTaskAction(task)">
              {{ task.actionText }}
            </el-button>
          </div>
        </div>
      </div>

      <div v-else class="empty-state-small">
        <el-icon><CircleCheck /></el-icon>
        <span>暂无待办事项</span>
      </div>
    </div>

    <!-- 创建项目对话框 -->
    <el-dialog
      v-model="showCreateDialog"
      title="新建项目"
      width="600px"
      :close-on-click-modal="false"
    >
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-width="100px"
      >
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
          <el-date-picker
            v-model="createForm.deadline"
            type="datetime"
            placeholder="选择投标截止时间"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="handleCreateSubmit">
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useProjectStore } from '@/stores/project'
import { documentApi, reportApi, requirementApi, riskApi } from '@/api'
import { ElMessage } from 'element-plus'
import { Plus, ArrowRight, FolderOpened, Warning, CircleCheck, Document, Calendar, Location } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance, FormRules } from 'element-plus'
import type { Project } from '@/types'

const router = useRouter()
const authStore = useAuthStore()
const projectStore = useProjectStore()

const user = computed(() => authStore.user)

const greeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 12) return '早上好'
  if (hour < 18) return '下午好'
  return '晚上好'
})

// 统计数据
const stats = reactive({
  activeProjects: 0,
  pendingRisks: 0,
  completedReports: 0,
  documents: 0,
})

// 最近项目
const recentProjects = ref<Project[]>([])
const projectPhases = ref<Record<string, string>>({})

// 待办事项
type DashboardTask = { id: string; type: 'risk' | 'match' | 'decision'; title: string; description: string; actionText: string; projectId: string }
const pendingTasks = ref<DashboardTask[]>([])

// 创建项目
const showCreateDialog = ref(false)
const creating = ref(false)
const createFormRef = ref<FormInstance>()

const createForm = reactive({
  name: '',
  purchaser: '',
  project_type: '',
  region: '',
  deadline: null as Date | null,
})

const createRules: FormRules = {
  name: [{ required: true, message: '请输入项目名称', trigger: 'blur' }],
  purchaser: [{ required: true, message: '请输入招标人', trigger: 'blur' }],
  project_type: [{ required: true, message: '请选择项目类型', trigger: 'change' }],
  region: [{ required: true, message: '请输入地区', trigger: 'blur' }],
  deadline: [{ required: true, message: '请选择投标截止时间', trigger: 'change' }],
}

const handleCreateProject = () => {
  showCreateDialog.value = true
}

const handleCreateSubmit = async () => {
  if (!createFormRef.value) return

  try {
    await createFormRef.value.validate()
    creating.value = true

    const project = await projectStore.createProject({
      ...createForm,
      deadline: createForm.deadline?.toISOString(),
      status: 'DRAFT',
    } as Partial<Project>)

    ElMessage.success('项目创建成功')
    showCreateDialog.value = false
    router.push(`/projects/${project.id}`)
  } catch (error) {
    // validation failed
  } finally {
    creating.value = false
  }
}

const getStatusDot = (status: string) => {
  const map: Record<string, string> = {
    DRAFT: 'draft',
    ACTIVE: 'active',
    ARCHIVED: 'pending',
  }
  return map[status] || 'pending'
}

const getStatusClass = (status: string) => {
  const map: Record<string, string> = {
    DRAFT: 'draft',
    ACTIVE: 'active',
    ARCHIVED: 'pending',
  }
  return map[status] || 'pending'
}

const getStatusText = (status: string) => {
  const map: Record<string, string> = {
    DRAFT: '草稿',
    ACTIVE: '进行中',
    ARCHIVED: '已归档',
  }
  return map[status] || status
}

const formatDate = (date: string) => {
  return dayjs(date).format('MM/DD')
}

const getBaseProjectPhase = (status?: string) => ({
  DRAFT: '待上传招标文件', ACTIVE: '投标准备中', ARCHIVED: '项目已归档',
} as Record<string, string>)[status || ''] || '状态待更新'

const getProjectPhase = (project: Project) => projectPhases.value[project.id] || getBaseProjectPhase(project.status)

const getTaskIcon = (type: string) => {
  const map: Record<string, any> = {
    risk: Warning,
    match: CircleCheck,
    decision: Document,
  }
  return map[type] || Document
}

const handleTaskAction = (task: typeof pendingTasks.value[0]) => {
  router.push(`/projects/${task.projectId}`)
}

onMounted(async () => {
  // 获取最近项目
  try {
    const response = await projectStore.fetchProjects()
    if (Array.isArray(response)) {
      recentProjects.value = response
      const results = await Promise.allSettled(response.map(async (project) => ({
        project,
        report: await reportApi.latest(project.id),
        documents: await documentApi.list(project.id),
        requirements: await requirementApi.list(project.id),
        risks: await riskApi.list(project.id),
      })))
      const phases: Record<string, string> = {}
      const tasks: DashboardTask[] = []
      let readyDocuments = 0
      let pendingRisks = 0
      let completedReports = 0
      for (const result of results) {
        if (result.status !== 'fulfilled') continue
        const { project, report, documents, requirements, risks } = result.value
        phases[project.id] = report?.status === 'READY'
          ? '报告已生成'
          : result.value.report?.status === 'GENERATING'
            ? '报告生成中'
            : getBaseProjectPhase(project.status)
        readyDocuments += documents.filter((document: any) => document.parse_status === 'READY').length
        completedReports += report?.status === 'READY' ? 1 : 0
        const projectPendingRisks = risks.filter(risk => risk.status === 'PENDING')
        pendingRisks += projectPendingRisks.length
        const priorityRequirements = requirements.filter(requirement => requirement.review_status === 'PENDING')
        if (priorityRequirements.length) tasks.push({ id: `review-${project.id}`, projectId: project.id, type: 'match', title: '需求待复核', description: `「${project.name}」有 ${priorityRequirements.length} 条关键需求待确认`, actionText: '进入复核' })
        if (projectPendingRisks.length) tasks.push({ id: `risk-${project.id}`, projectId: project.id, type: 'risk', title: '风险待处理', description: `「${project.name}」有 ${projectPendingRisks.length} 条风险待处理`, actionText: '查看项目' })
      }
      projectPhases.value = phases
      pendingTasks.value = tasks
      stats.activeProjects = response.filter(project => project.status === 'ACTIVE').length
      stats.pendingRisks = pendingRisks
      stats.completedReports = completedReports
      stats.documents = readyDocuments
    }
  } catch (error) {
    // ignore
  }
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.welcome-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-8);
  padding: var(--spacing-6);
  background: linear-gradient(135deg, var(--color-primary), var(--color-secondary));
  border-radius: var(--radius-xl);
  color: white;
}

.welcome-content {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-1);
}

.welcome-title {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: white;
  margin: 0;
}

.welcome-subtitle {
  font-size: var(--font-size-sm);
  color: rgba(255, 255, 255, 0.8);
  margin: 0;
}

.welcome-section :deep(.el-button) {
  background: white;
  color: var(--color-primary);
  border: none;
  font-weight: 600;
  box-shadow: var(--shadow-lg);
}

.welcome-section :deep(.el-button:hover) {
  transform: translateY(-2px);
  box-shadow: var(--shadow-xl);
}

/* 统计卡片 */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--spacing-5);
  margin-bottom: var(--spacing-8);
}

.stat-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: var(--spacing-5);
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.stat-icon {
  width: 52px;
  height: 52px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  box-shadow: var(--shadow-md);
}

.stat-icon-primary {
  background: linear-gradient(135deg, var(--color-accent), #0284c7);
  color: white;
}

.stat-icon-warning {
  background: linear-gradient(135deg, var(--color-warning), #d97706);
  color: white;
}

.stat-icon-success {
  background: linear-gradient(135deg, var(--color-success), #059669);
  color: white;
}

.stat-icon-info {
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  color: white;
}

.stat-content {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: var(--font-size-2xl);
  font-weight: 700;
  color: var(--color-text-primary);
  line-height: 1.2;
}

.stat-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

/* 区块 */
.section {
  margin-bottom: var(--spacing-8);
}

.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-4);
}

.section-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--color-text-primary);
}

/* 项目卡片 */
.projects-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--spacing-5);
}

.project-card {
  cursor: pointer;
  padding: var(--spacing-5);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.project-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.project-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-3);
}

.project-status {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.project-code {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.project-name {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.project-purchaser {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-4);
}

.project-meta {
  display: flex;
  gap: var(--spacing-4);
  margin-bottom: var(--spacing-4);
}

.project-meta-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.project-meta-item .el-icon {
  font-size: 14px;
}

.project-progress {
  padding-top: var(--spacing-3);
  border-top: 1px solid var(--color-divider);
}

.progress-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: var(--spacing-2);
}

.progress-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.progress-value {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--color-text-primary);
}

/* 待办事项 */
.tasks-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
}

.task-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  padding: var(--spacing-4);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.task-item:hover {
  transform: translateX(4px);
  box-shadow: var(--shadow-md);
}

.task-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  box-shadow: var(--shadow-sm);
}

.task-icon-risk {
  background: linear-gradient(135deg, #fef3c7, #fde68a);
  color: var(--color-warning);
}

.task-icon-match {
  background: linear-gradient(135deg, #d1fae5, #a7f3d0);
  color: var(--color-success);
}

.task-icon-decision {
  background: linear-gradient(135deg, #e0e7ff, #c7d2fe);
  color: var(--color-accent);
}

.task-content {
  flex: 1;
}

.task-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.task-description {
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.task-action :deep(.el-button) {
  border-radius: var(--radius-md);
}

.empty-state-small {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-2);
  padding: var(--spacing-8);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  color: var(--color-text-muted);
  box-shadow: var(--shadow-sm);
}

/* 响应式 */
@media (max-width: 1200px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .projects-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .dashboard {
    padding: var(--spacing-4);
  }

  .stats-grid {
    grid-template-columns: 1fr;
  }

  .projects-grid {
    grid-template-columns: 1fr;
  }

  .welcome-section {
    flex-direction: column;
    align-items: flex-start;
    gap: var(--spacing-4);
    border-radius: var(--radius-lg);
  }
}
</style>
