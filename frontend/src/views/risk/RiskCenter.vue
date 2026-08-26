<template>
  <div class="risk-center">
    <div class="page-header">
      <div>
        <h1 class="page-title">风险中心</h1>
        <p class="page-subtitle">集中管理所有项目风险</p>
      </div>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-input v-model="searchKeyword" placeholder="搜索风险标题" :prefix-icon="Search" clearable style="width: 240px" />

      <el-select v-model="severityFilter" placeholder="风险等级" clearable style="width: 140px">
        <el-option label="全部" value="" />
        <el-option label="严重" value="CRITICAL" />
        <el-option label="高" value="HIGH" />
        <el-option label="中" value="MEDIUM" />
        <el-option label="低" value="LOW" />
      </el-select>

      <el-select v-model="statusFilter" placeholder="处理状态" clearable style="width: 140px">
        <el-option label="全部" value="" />
        <el-option label="待处理" value="PENDING" />
        <el-option label="已确认" value="CONFIRMED" />
        <el-option label="已解决" value="RESOLVED" />
        <el-option label="误报" value="FALSE_POSITIVE" />
      </el-select>

      <el-select v-model="typeFilter" placeholder="风险类型" clearable style="width: 140px">
        <el-option label="全部" value="" />
        <el-option label="资格" value="QUALIFICATION" />
        <el-option label="合规" value="COMPLIANCE" />
        <el-option label="格式" value="FORMAT" />
        <el-option label="时间" value="TIME" />
        <el-option label="财务" value="FINANCIAL" />
        <el-option label="技术" value="TECHNICAL" />
      </el-select>
    </div>

    <!-- 统计卡片 -->
    <div class="stats-row">
      <div class="stat-card stat-critical">
        <span class="stat-value">{{ stats.critical }}</span>
        <span class="stat-label">严重风险</span>
      </div>
      <div class="stat-card stat-high">
        <span class="stat-value">{{ stats.high }}</span>
        <span class="stat-label">高风险</span>
      </div>
      <div class="stat-card stat-medium">
        <span class="stat-value">{{ stats.medium }}</span>
        <span class="stat-label">中风险</span>
      </div>
      <div class="stat-card stat-low">
        <span class="stat-value">{{ stats.low }}</span>
        <span class="stat-label">低风险</span>
      </div>
      <div class="stat-card stat-pending">
        <span class="stat-value">{{ stats.pending }}</span>
        <span class="stat-label">待处理</span>
      </div>
    </div>

    <!-- 风险列表 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中...</span>
    </div>

    <div v-else-if="risks.length" class="risks-list">
      <div v-for="risk in risks" :key="risk.id" class="risk-card card">
        <div class="risk-header">
          <div class="risk-left">
            <span :class="['severity-badge', `severity-${risk.severity.toLowerCase()}`]">
              {{ getSeverityText(risk.severity) }}
            </span>
            <span class="risk-type">{{ getRiskTypeText(risk.risk_type) }}</span>
            <span :class="['badge', `badge-${getStatusClass(risk.status)}`]">
              {{ getStatusText(risk.status) }}
            </span>
          </div>
          <div class="risk-project">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ risk.project_name }}</span>
          </div>
        </div>

        <h3 class="risk-title">{{ risk.title }}</h3>
        <p class="risk-description">{{ risk.description }}</p>

        <div class="risk-evidence" v-if="risk.evidence_ids?.length">
          <el-icon><Link /></el-icon>
          <span>有证据支撑</span>
        </div>

        <div class="risk-footer">
          <span class="risk-time">{{ formatDate(risk.created_at) }}</span>
          <div class="risk-actions">
            <el-button text size="small" @click="viewDetail(risk)">查看详情</el-button>
            <el-button v-if="risk.status === 'PENDING'" type="primary" size="small" @click="handleReview(risk)">
              复核
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><CircleCheck /></el-icon>
      <h3 class="empty-state-title">暂无风险</h3>
      <p class="empty-state-description">所有项目运行正常，未发现风险</p>
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

    <!-- 复核对话框 -->
    <el-dialog v-model="showReviewDialog" title="风险复核" width="500px">
      <el-form ref="reviewFormRef" :model="reviewForm" label-width="100px">
        <el-form-item label="风险等级">
          <span :class="['severity-badge', `severity-${reviewForm.severity?.toLowerCase()}`]">
            {{ getSeverityText(reviewForm.severity) }}
          </span>
        </el-form-item>

        <el-form-item label="处理状态" prop="status">
          <el-select v-model="reviewForm.status" style="width: 100%">
            <el-option label="已确认" value="CONFIRMED" />
            <el-option label="已解决" value="RESOLVED" />
            <el-option label="误报" value="FALSE_POSITIVE" />
            <el-option label="已忽略" value="IGNORED" />
          </el-select>
        </el-form-item>

        <el-form-item label="处理说明" prop="resolution">
          <el-input v-model="reviewForm.resolution" type="textarea" :rows="4" placeholder="请输入处理说明" />
        </el-form-item>
      </el-form>

      <template #footer>
        <el-button @click="showReviewDialog = false">取消</el-button>
        <el-button type="primary" :loading="reviewing" @click="handleSubmitReview">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search, FolderOpened, Link, CircleCheck, Loading } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance } from 'element-plus'
import { projectApi, riskApi } from '@/api'
import type { Risk } from '@/types'

const router = useRouter()

// 后端风险接口是项目级，这里逐项目拉取后聚合，并补上项目名
type RiskWithProject = Risk & { project_name: string }

const loading = ref(false)
const allRisks = ref<RiskWithProject[]>([])
const currentPage = ref(1)
const pageSize = ref(20)

const searchKeyword = ref('')
const severityFilter = ref('')
const statusFilter = ref('')
const typeFilter = ref('')

const filteredRisks = computed(() =>
  allRisks.value.filter((risk) => {
    if (searchKeyword.value && !risk.title.includes(searchKeyword.value)) return false
    if (severityFilter.value && risk.severity !== severityFilter.value) return false
    if (statusFilter.value && risk.status !== statusFilter.value) return false
    if (typeFilter.value && risk.risk_type !== typeFilter.value) return false
    return true
  }),
)

const total = computed(() => filteredRisks.value.length)

const risks = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return filteredRisks.value.slice(start, start + pageSize.value)
})

const stats = reactive({
  critical: 0,
  high: 0,
  medium: 0,
  low: 0,
  pending: 0,
})

const refreshStats = () => {
  stats.critical = allRisks.value.filter((r) => r.severity === 'CRITICAL').length
  stats.high = allRisks.value.filter((r) => r.severity === 'HIGH').length
  stats.medium = allRisks.value.filter((r) => r.severity === 'MEDIUM').length
  stats.low = allRisks.value.filter((r) => r.severity === 'LOW').length
  stats.pending = allRisks.value.filter((r) => r.status === 'PENDING').length
}

const showReviewDialog = ref(false)
const reviewing = ref(false)

const reviewFormRef = ref<FormInstance>()
const reviewForm = reactive({
  id: '',
  project_id: '',
  severity: '',
  status: '',
  resolution: '',
})

const fetchRisks = async () => {
  loading.value = true
  try {
    const projects = await projectApi.list()
    const results = await Promise.allSettled(
      projects.map(async (p) => {
        const projectRisks = await riskApi.list(p.id)
        return projectRisks.map((r) => ({ ...r, project_name: p.name }))
      }),
    )
    allRisks.value = results
      .filter((r): r is PromiseFulfilledResult<RiskWithProject[]> => r.status === 'fulfilled')
      .flatMap((r) => r.value)
    refreshStats()
  } finally {
    loading.value = false
  }
}

const handleSizeChange = () => {
  currentPage.value = 1
}

const handlePageChange = () => {
  // 分页为前端切片，无需重新请求
}

const viewDetail = (risk: RiskWithProject) => {
  router.push(`/projects/${risk.project_id}`)
}

const handleReview = (risk: RiskWithProject) => {
  reviewForm.id = risk.id
  reviewForm.project_id = risk.project_id
  reviewForm.severity = risk.severity
  reviewForm.status = 'CONFIRMED'
  reviewForm.resolution = ''
  showReviewDialog.value = true
}

const handleSubmitReview = async () => {
  if (!reviewForm.resolution) {
    ElMessage.warning('请输入处理说明')
    return
  }

  reviewing.value = true
  try {
    await riskApi.review(reviewForm.project_id, reviewForm.id, {
      status: reviewForm.status,
      resolution: reviewForm.resolution,
    })
    ElMessage.success('复核成功')
    showReviewDialog.value = false
    await fetchRisks()
  } catch {} finally {
    reviewing.value = false
  }
}

const getSeverityText = (s: string) => ({ CRITICAL: '严重', HIGH: '高', MEDIUM: '中', LOW: '低', INFO: '提示' }[s] || s)
const getRiskTypeText = (t: string) => ({ QUALIFICATION: '资格', COMPLIANCE: '合规', FORMAT: '格式', TIME: '时间', FINANCIAL: '财务', TECHNICAL: '技术', COMMERCIAL: '商务', DOCUMENT: '文档' }[t] || t)
const getStatusClass = (s: string) => ({ PENDING: 'draft', CONFIRMED: 'warning', RESOLVED: 'active', FALSE_POSITIVE: 'failed', IGNORED: 'failed' }[s] || 'draft')
const getStatusText = (s: string) => ({ PENDING: '待处理', CONFIRMED: '已确认', RESOLVED: '已解决', FALSE_POSITIVE: '误报', IGNORED: '已忽略' }[s] || s)
const formatDate = (d: string) => dayjs(d).format('YYYY-MM-DD HH:mm')

onMounted(() => {
  fetchRisks()
})
</script>

<style scoped>

<style scoped>
.risk-center {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.page-header {
  margin-bottom: var(--spacing-6);
}

.filter-bar {
  display: flex;
  gap: var(--spacing-3);
  margin-bottom: var(--spacing-6);
  padding: var(--spacing-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
}

.stats-row {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: var(--spacing-5);
  margin-bottom: var(--spacing-6);
}

.stat-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: var(--spacing-5);
  text-align: center;
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.stat-value {
  display: block;
  font-size: var(--font-size-2xl);
  font-weight: 700;
  margin-bottom: var(--spacing-1);
  line-height: 1.2;
}

.stat-label {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.stat-critical .stat-value { color: #991B1B; }
.stat-high .stat-value { color: #92400E; }
.stat-medium .stat-value { color: #3730A3; }
.stat-low .stat-value { color: #065F46; }
.stat-pending .stat-value { color: var(--color-warning); }

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-2);
  padding: var(--spacing-16);
  color: var(--color-text-muted);
}

.risks-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-4);
}

.risk-card {
  padding: var(--spacing-5);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.risk-card:hover {
  transform: translateX(4px);
  box-shadow: var(--shadow-md);
}

.risk-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-3);
}

.risk-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.severity-badge {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-md);
  font-weight: 500;
}

.severity-critical { background: linear-gradient(135deg, #fee2e2, #fecaca); color: #991B1B; }
.severity-high { background: linear-gradient(135deg, #fef3c7, #fde68a); color: #92400E; }
.severity-medium { background: linear-gradient(135deg, #e0e7ff, #c7d2fe); color: #3730A3; }
.severity-low { background: linear-gradient(135deg, #d1fae5, #a7f3d0); color: #065F46; }
.severity-info { background: linear-gradient(135deg, #e0e7ff, #c7d2fe); color: #3730A3; }

.risk-type {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.risk-project {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.risk-title {
  font-size: var(--font-size-base);
  font-weight: 600;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-2);
}

.risk-description {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-3);
  line-height: 1.6;
}

.risk-evidence {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  font-size: var(--font-size-xs);
  color: var(--color-success);
  margin-bottom: var(--spacing-3);
}

.risk-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: var(--spacing-3);
  border-top: 1px solid var(--color-divider);
}

.risk-time {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.risk-actions {
  display: flex;
  gap: var(--spacing-2);
}

.pagination {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--spacing-4);
}

@media (max-width: 1024px) {
  .stats-row {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 768px) {
  .stats-row {
    grid-template-columns: repeat(2, 1fr);
  }

  .filter-bar {
    flex-wrap: wrap;
  }
}
</style>
