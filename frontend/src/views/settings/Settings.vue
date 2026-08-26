<template>
  <div class="settings-page">
    <div class="page-header">
      <h1 class="page-title">系统设置</h1>
      <p class="page-subtitle">管理用户、角色和系统配置</p>
    </div>

    <div class="settings-content">
      <el-tabs v-model="activeTab" tab-position="left">
        <el-tab-pane label="个人设置" name="profile">
          <h3 class="tab-title">个人设置</h3>
          <div class="settings-form">
            <el-form label-width="120px">
              <el-form-item label="用户名">
                <el-input v-model="userForm.username" disabled />
              </el-form-item>
              <el-form-item label="角色">
                <el-input :value="getRoleText(userForm.role)" disabled />
              </el-form-item>
              <el-form-item label="新密码">
                <el-input v-model="userForm.new_password" type="password" show-password placeholder="留空则不修改" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="handleUpdatePassword">保存</el-button>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="isAdmin" label="用户管理" name="users">
          <h3 class="tab-title">用户管理</h3>
          <div class="users-section">
            <div class="section-header">
              <el-button type="primary" :icon="Plus" @click="showUserDialog = true">添加用户</el-button>
            </div>
            <el-table :data="users" stripe>
              <el-table-column prop="username" label="用户名" />
              <el-table-column prop="roles" label="角色">
                <template #default="{ row }">
                  {{ getRoleText(row.roles?.[0]) }}
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态">
                <template #default="{ row }">
                  <span :class="['status-badge', row.status]">
                    {{ row.status === 'ACTIVE' ? '启用' : '禁用' }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="120">
                <template #default>
                  <el-button text size="small">编辑</el-button>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="isAdmin" label="规则管理" name="rules">
          <h3 class="tab-title">规则管理</h3>
          <p class="tab-description">管理和配置投标风险检查规则</p>
          <div class="rules-list">
            <div v-for="rule in rules" :key="rule.id" class="rule-card card">
              <div class="rule-header">
                <span class="rule-name">{{ rule.name }}</span>
                <span :class="['status-badge', rule.status]">
                  {{ rule.status === 'active' ? '启用' : '停用' }}
                </span>
              </div>
              <p class="rule-description">{{ rule.description }}</p>
              <div class="rule-meta">
                <span>类型：{{ rule.risk_type }}</span>
                <span>等级：{{ rule.severity }}</span>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane v-if="isAdmin" label="审计日志" name="audit">
          <h3 class="tab-title">审计日志</h3>
          <p class="tab-description">查看系统操作记录</p>
          <el-table :data="auditLogs" stripe>
            <el-table-column prop="action" label="操作" />
            <el-table-column prop="actor" label="操作人" />
            <el-table-column prop="target" label="操作对象" />
            <el-table-column prop="created_at" label="时间">
              <template #default="{ row }">
                {{ formatDate(row.created_at) }}
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane v-if="isAdmin" label="服务状态" name="system">
          <div class="system-status-heading">
            <div>
              <h3 class="tab-title">服务状态</h3>
              <p class="tab-description">检查当前环境的关键依赖是否已就绪。</p>
            </div>
            <el-button :icon="Refresh" :loading="isCheckingHealth" @click="loadReadiness">
              刷新状态
            </el-button>
          </div>

          <el-alert
            v-if="healthError"
            title="暂时无法获取服务状态"
            type="warning"
            :closable="false"
            show-icon
            class="health-alert"
          >
            请确认 API 服务已启动后再刷新。
          </el-alert>

          <template v-else>
            <div class="overall-status" :class="isSystemReady ? 'ready' : 'not-ready'">
              <el-icon><CircleCheckFilled v-if="isSystemReady" /><CircleCloseFilled v-else /></el-icon>
              <div>
                <strong>{{ isSystemReady ? '系统服务正常' : '部分服务未就绪' }}</strong>
                <p>{{ isSystemReady ? '核心依赖与 AI 服务均可用。' : '请查看下方未就绪项并检查部署服务。' }}</p>
              </div>
            </div>

            <div class="health-grid">
              <div v-for="item in healthItems" :key="item.key" class="health-card">
                <div class="health-icon" :class="item.available ? 'available' : 'unavailable'">
                  <el-icon><CircleCheckFilled v-if="item.available" /><CircleCloseFilled v-else /></el-icon>
                </div>
                <div>
                  <div class="health-name">{{ item.label }}</div>
                  <div class="health-detail">{{ item.available ? '运行正常' : '暂不可用' }}</div>
                </div>
              </div>
            </div>
            <p v-if="lastCheckedAt" class="health-time">上次检查：{{ formatDate(lastCheckedAt) }}</p>
          </template>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { systemApi, type ReadinessStatus } from '@/api'

const authStore = useAuthStore()

const activeTab = ref('profile')
const showUserDialog = ref(false)

const userForm = ref({
  username: authStore.user?.username || '',
  role: authStore.user?.roles?.[0] || '',
  new_password: '',
})

const users = ref<any[]>([])
const rules = ref<any[]>([])
const auditLogs = ref<any[]>([])
const readiness = ref<ReadinessStatus | null>(null)
const isCheckingHealth = ref(false)
const healthError = ref(false)
const lastCheckedAt = ref<Date | null>(null)

const isAdmin = computed(() =>
  authStore.user?.roles?.some((role) => role.toUpperCase() === 'SYSTEM_ADMIN') ?? false,
)

const healthItems = computed(() => {
  const checks = readiness.value?.checks || {}
  return [
    { key: 'postgres', label: 'PostgreSQL', available: checks.postgres === true },
    { key: 'redis', label: 'Redis', available: checks.redis === true },
    { key: 'minio', label: '对象存储', available: checks.minio === true },
    { key: 'pgvector', label: '向量检索', available: checks.pgvector === true },
    { key: 'mineru', label: '文档解析', available: checks.mineru === true },
    { key: 'ai', label: 'AI 服务', available: readiness.value?.ai_available === true },
  ]
})

const isSystemReady = computed(() =>
  readiness.value?.status === 'ok' && healthItems.value.every((item) => item.available),
)

const getRoleText = (role: string) => ({
  system_admin: '系统管理员',
  SYSTEM_ADMIN: '系统管理员',
  project_owner: '项目负责人',
  PROJECT_OWNER: '项目负责人',
  bid_specialist: '投标专员',
  BID_SPECIALIST: '投标专员',
  legal_compliance: '法务/合规',
  LEGAL_COMPLIANCE: '法务/合规',
  material_manager: '企业材料管理员',
  MATERIAL_ADMIN: '企业材料管理员',
  readonly: '只读',
  READ_ONLY: '只读',
}[role] || role)

const handleUpdatePassword = () => {
  ElMessage.success('密码已更新')
}

const formatDate = (d: string | Date) => dayjs(d).format('YYYY-MM-DD HH:mm')

const loadReadiness = async () => {
  isCheckingHealth.value = true
  healthError.value = false
  try {
    readiness.value = await systemApi.getReadiness()
    lastCheckedAt.value = new Date()
  } catch {
    readiness.value = null
    healthError.value = true
  } finally {
    isCheckingHealth.value = false
  }
}

onMounted(() => {
  if (isAdmin.value) void loadReadiness()
})

</script>

<style scoped>
.settings-page {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--spacing-6);
}

.page-header {
  margin-bottom: var(--spacing-6);
}

.settings-content {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
}

.tab-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  margin-bottom: var(--spacing-4);
}

.tab-description {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-4);
}

.settings-form {
  max-width: 480px;
}

.section-header {
  margin-bottom: var(--spacing-4);
}

.system-status-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--spacing-4);
}

.health-alert {
  margin-top: var(--spacing-4);
}

.overall-status {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  margin: var(--spacing-4) 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.overall-status.ready {
  background: #f0fdf4;
  border-color: #bbf7d0;
}

.overall-status.not-ready {
  background: #fef2f2;
  border-color: #fecaca;
}

.overall-status .el-icon {
  font-size: 24px;
}

.overall-status.ready .el-icon { color: #16a34a; }
.overall-status.not-ready .el-icon { color: #dc2626; }

.overall-status strong { color: var(--color-text-primary); }
.overall-status p {
  margin: var(--spacing-1) 0 0;
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
}

.health-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--spacing-3);
}

.health-card {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: #fff;
}

.health-icon { font-size: 20px; }
.health-icon.available { color: #16a34a; }
.health-icon.unavailable { color: #dc2626; }
.health-name { font-weight: 600; color: var(--color-text-primary); }
.health-detail { margin-top: 2px; color: var(--color-text-muted); font-size: var(--font-size-xs); }
.health-time { margin: var(--spacing-4) 0 0; color: var(--color-text-muted); font-size: var(--font-size-xs); }

.status-badge {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  border-radius: var(--radius-full);
}

.status-badge.active, .status-badge.published {
  background: linear-gradient(135deg, #d1fae5, #a7f3d0);
  color: #065f46;
}

.status-badge.inactive, .status-badge.disabled {
  background: linear-gradient(135deg, #fee2e2, #fecaca);
  color: #991b1b;
}

.rules-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--spacing-5);
}

.rule-card {
  padding: var(--spacing-4);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--transition-base);
}

.rule-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.rule-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--spacing-2);
}

.rule-name {
  font-weight: 600;
}

.rule-description {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  margin-bottom: var(--spacing-2);
  line-height: 1.5;
}

.rule-meta {
  display: flex;
  gap: var(--spacing-4);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

@media (max-width: 768px) {
  .rules-list {
    grid-template-columns: 1fr;
  }

  .health-grid { grid-template-columns: 1fr; }
}
</style>
