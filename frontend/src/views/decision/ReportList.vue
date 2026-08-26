<template>
  <div class="report-list">
    <div class="page-header">
      <div>
        <h1 class="page-title">报告中心</h1>
        <p class="page-subtitle">查看和管理所有分析报告</p>
      </div>
    </div>

    <div v-if="reports.length" class="reports-grid">
      <div v-for="report in reports" :key="report.id" class="report-card card">
        <div class="report-header">
          <div class="report-project">
            <el-icon><FolderOpened /></el-icon>
            <span>{{ report.project_name }}</span>
          </div>
          <span :class="['badge', `badge-${getStatusClass(report.status)}`]">
            {{ getStatusText(report.status) }}
          </span>
        </div>

        <h3 class="report-title">投标分析报告 v{{ report.version_no }}</h3>
        <p class="report-meta">
          生成时间：{{ formatDate(report.generated_at || report.created_at) }}
        </p>

        <div v-if="report.status === 'READY'" class="report-actions">
          <el-button size="small" @click="openReport(report)">查看摘要</el-button>
          <el-button type="primary" size="small" :loading="downloadingId === report.id" @click="handleDownload(report, 'docx')">
            <el-icon><Document /></el-icon>
            DOCX
          </el-button>
          <el-button size="small" :loading="downloadingId === report.id" @click="handleDownload(report, 'pdf')">
            <el-icon><Document /></el-icon>
            PDF
          </el-button>
          <el-button size="small" :loading="downloadingId === report.id" @click="handleDownload(report, 'md')">
            <el-icon><Document /></el-icon>
            MD
          </el-button>
        </div>

        <div v-else-if="report.status === 'FAILED'" class="report-error">
          <el-icon><Warning /></el-icon>
          <span>{{ report.error_message || '生成失败' }}</span>
          <el-button text size="small" :loading="retryingId === report.project_id" @click="handleRetry(report)">重试</el-button>
        </div>

        <div v-else class="report-generating">
          <el-progress :percentage="50" :show-text="false" />
          <span>生成中...</span>
        </div>
      </div>
    </div>

    <el-drawer v-model="showDetail" :title="selectedReport ? `${selectedReport.project_name} · 报告摘要` : '报告摘要'" size="680px">
      <template v-if="selectedReport">
        <el-alert title="以下内容来自已生成报告章节；每章均保留原文证据关联。" type="info" :closable="false" />
        <el-collapse class="report-sections" :model-value="selectedReport.sections?.map(section => section.section_code)">
          <el-collapse-item v-for="section in selectedReport.sections" :key="section.section_code" :name="section.section_code">
            <template #title>{{ section.order_no }}. {{ section.section_code }} · {{ section.evidence_ids.length }} 条依据</template>
            <pre class="section-content">{{ section.content_markdown }}</pre>
          </el-collapse-item>
        </el-collapse>
      </template>
    </el-drawer>

    <div v-if="!reports.length" class="empty-state">
      <el-icon class="empty-state-icon"><Document /></el-icon>
      <h3 class="empty-state-title">暂无报告</h3>
      <p class="empty-state-description">在项目详情页生成分析报告</p>
      <el-button type="primary" @click="router.push('/projects')">前往项目</el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { FolderOpened, Document, Warning } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { projectApi, reportApi } from '@/api'
import type { Report } from '@/types'

const router = useRouter()

// 后端只有"项目最新报告"接口，这里逐项目拉取并补上项目名
type ReportWithProject = Report & { project_name: string }

const reports = ref<ReportWithProject[]>([])
const downloadingId = ref<string | null>(null)
const retryingId = ref<string | null>(null)
const selectedReport = ref<ReportWithProject | null>(null)
const showDetail = ref(false)

const openReport = async (report: ReportWithProject) => {
  try {
    selectedReport.value = { ...report, ...(await reportApi.get(report.id)) }
    showDetail.value = true
  } catch (err: any) {
    ElMessage.error(err?.message || '加载报告摘要失败')
  }
}

const handleDownload = async (report: ReportWithProject, format: 'docx' | 'pdf' | 'md') => {
  downloadingId.value = report.id
  try {
    await reportApi.download(report.id, format)
  } catch (err: any) {
    ElMessage.error(err?.message || '下载失败')
  } finally {
    downloadingId.value = null
  }
}

const handleRetry = async (report: ReportWithProject) => {
  retryingId.value = report.project_id
  try {
    await reportApi.generate(report.project_id)
    ElMessage.success('已重新提交生成任务，请稍后刷新查看')
  } catch {} finally {
    retryingId.value = null
  }
}

const getStatusClass = (s: string) => ({ PENDING: 'draft', GENERATING: 'draft', READY: 'active', FAILED: 'failed' }[s] || 'draft')
const getStatusText = (s: string) => ({ PENDING: '待生成', GENERATING: '生成中', READY: '已完成', FAILED: '失败' }[s] || s)
const formatDate = (d: string) => dayjs(d).format('YYYY-MM-DD HH:mm')

onMounted(async () => {
  try {
    const projects = await projectApi.list()
    const results = await Promise.allSettled(
      projects.map(async (p) => {
        const report = await reportApi.latest(p.id)
        return report ? { ...report, project_name: p.name } : null
      }),
    )
    reports.value = results
      .filter((r): r is PromiseFulfilledResult<ReportWithProject | null> => r.status === 'fulfilled')
      .map((r) => r.value)
      .filter((r): r is ReportWithProject => r !== null)
  } catch {
    // 拦截器已提示
  }
})
</script>

<style scoped>
.report-list {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.page-header {
  margin-bottom: var(--spacing-6);
}

.reports-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--spacing-5);
}

.report-card {
  padding: var(--spacing-5);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
  border: 1px solid var(--color-border);
  position: relative;
  overflow: hidden;
}

.report-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--color-accent), var(--color-info));
  opacity: 0;
  transition: opacity var(--transition-base);
}

.report-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}

.report-card:hover::before {
  opacity: 1;
}

.report-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-3);
}

.report-project {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.report-title {
  font-size: var(--font-size-lg);
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-2);
  letter-spacing: -0.01em;
}

.report-meta {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-4);
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.report-meta::before {
  content: '';
  display: inline-block;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--color-border-hover);
}

.report-actions {
  display: flex;
  gap: var(--spacing-2);
}

.report-error {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-3);
  background: linear-gradient(135deg, #fee2e2, #fecaca);
  border-radius: var(--radius-lg);
  color: #991b1b;
  font-size: var(--font-size-sm);
}

.report-error .el-icon {
  color: var(--color-destructive);
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

.report-sections { margin-top: var(--spacing-4); }
.section-content { white-space: pre-wrap; font: inherit; line-height: 1.75; color: var(--color-text-secondary); }

@media (max-width: 1024px) {
  .reports-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .reports-grid {
    grid-template-columns: 1fr;
  }
}
</style>
