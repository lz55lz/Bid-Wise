<template>
  <div class="material-library">
    <div class="page-header">
      <div>
        <h1 class="page-title">企业管理</h1>
        <p class="page-subtitle">查看企业详情和资质材料</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreateEnterpriseDialog">
        新建企业
      </el-button>
    </div>

    <!-- 企业列表 -->
    <div v-if="loading" class="loading-container">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>加载中...</span>
    </div>

    <div v-else-if="enterprises.length" class="enterprise-table">
      <el-table :data="enterprises" stripe @expand-change="(onEnterpriseExpand as any)">
        <el-table-column type="expand" width="50">
          <template #default="{ row }">
            <div class="enterprise-detail" v-loading="expandedIds.has(row.id) && detailLoading.has(row.id)">
              <!-- 基本信息 -->
              <div class="detail-section">
                <h4 class="detail-title">基本信息</h4>
                <div class="info-grid">
                  <div class="info-item">
                    <span class="info-label">企业名称</span>
                    <span class="info-value">{{ row.name }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">信用代码</span>
                    <span class="info-value">{{ row.credit_code || '-' }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">企业类型</span>
                    <span class="info-value">{{ getEnterpriseTypeText(row.enterprise_type) }}</span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">状态</span>
                    <span :class="['status-badge', row.status === 'ACTIVE' ? 'active' : 'inactive']">
                      {{ row.status === 'ACTIVE' ? '正常' : '停用' }}
                    </span>
                  </div>
                  <div class="info-item">
                    <span class="info-label">创建时间</span>
                    <span class="info-value">{{ formatDate(row.created_at) }}</span>
                  </div>
                </div>
              </div>

              <!-- 材料列表 -->
              <div class="detail-section" v-if="enterpriseMaterials[row.id]">
                <div class="section-header">
                  <h4 class="detail-title">资质材料</h4>
                  <div class="type-tabs">
                    <span
                      v-for="t in materialTypes"
                      :key="t.value"
                      :class="['type-tab', { active: activeMaterialType[row.id] === t.value }]"
                      @click="activeMaterialType[row.id] = t.value"
                    >
                      {{ t.label }}
                      <span class="type-count">{{ getTypeCount(row.id, t.value) }}</span>
                    </span>
                  </div>
                </div>

                <div v-if="getFilteredMaterials(row.id).length" class="materials-list">
                  <div v-for="m in getFilteredMaterials(row.id)" :key="m.id" class="material-item">
                    <div class="material-info">
                      <span class="material-name">{{ m.name }}</span>
                      <span :class="['type-badge', `type-${m.material_type.toLowerCase()}`]">
                        {{ getMaterialTypeText(m.material_type) }}
                      </span>
                      <span v-if="m.level" class="material-level">{{ m.level }}</span>
                      <span v-if="m.material_no" class="material-no">{{ m.material_no }}</span>
                    </div>
                    <div class="material-meta">
                      <span v-if="m.valid_from || m.valid_to" :class="['validity', { expired: isExpired(m.valid_to) }]">
                        {{ formatDate(m.valid_from) }} 至 {{ formatDate(m.valid_to) }}
                        <span v-if="isExpired(m.valid_to)" class="expired-tag">已过期</span>
                      </span>
                      <span v-else class="no-expiry">长期有效</span>
                      <span :class="['status-badge', m.status === 'CONFIRMED' ? 'active' : 'inactive']">
                        {{ m.status === 'CONFIRMED' ? '有效' : '待确认' }}
                      </span>
                    </div>
                    <div class="material-actions">
                      <el-button text size="small" @click="openEditMaterialDialog(m)">编辑</el-button>
                      <el-button text size="small" type="danger" @click="handleDeleteMaterial(m)">删除</el-button>
                    </div>
                  </div>
                </div>
                <div v-else class="empty-materials">
                  <span>暂无{{ activeMaterialType[row.id] ? getMaterialTypeText(activeMaterialType[row.id]) + '材料' : '材料' }}</span>
                </div>

                <!-- 添加材料按钮 -->
                <div class="add-material-bar">
                  <el-button size="small" @click="openAddMaterialDialog(row.id)">+ 添加材料</el-button>
                </div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="name" label="企业名称" min-width="180" />

        <el-table-column prop="credit_code" label="信用代码" width="180">
          <template #default="{ row }">
            <span class="credit-code">{{ row.credit_code || '-' }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="enterprise_type" label="类型" width="120">
          <template #default="{ row }">
            <span class="type-tag">{{ getEnterpriseTypeText(row.enterprise_type) }}</span>
          </template>
        </el-table-column>

        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <span :class="['status-badge', row.status === 'ACTIVE' ? 'active' : 'inactive']">
              {{ row.status === 'ACTIVE' ? '正常' : '停用' }}
            </span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button text size="small" @click="openEditEnterpriseDialog(row as Enterprise)">编辑</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-else class="empty-state">
      <el-icon class="empty-state-icon"><OfficeBuilding /></el-icon>
      <h3 class="empty-state-title">暂无企业</h3>
      <p class="empty-state-description">创建企业后可管理其资质材料</p>
      <el-button type="primary" @click="openCreateEnterpriseDialog">新建企业</el-button>
    </div>

    <!-- 新建/编辑企业对话框 -->
    <el-dialog v-model="showEnterpriseDialog" :title="isEditingEnterprise ? '编辑企业' : '新建企业'" width="500px" :close-on-click-modal="false">
      <el-form ref="enterpriseFormRef" :model="enterpriseForm" :rules="enterpriseRules" label-width="100px">
        <el-form-item label="企业名称" prop="name">
          <el-input v-model="enterpriseForm.name" placeholder="请输入企业名称" />
        </el-form-item>
        <el-form-item label="信用代码" prop="credit_code">
          <el-input v-model="enterpriseForm.credit_code" placeholder="统一社会信用代码" />
        </el-form-item>
        <el-form-item label="企业类型" prop="enterprise_type">
          <el-select v-model="enterpriseForm.enterprise_type" style="width: 100%">
            <el-option label="母公司" value="PARENT" />
            <el-option label="子公司" value="SUBSIDIARY" />
            <el-option label="独立公司" value="INDEPENDENT" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showEnterpriseDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingEnterprise" @click="handleSaveEnterprise">
          {{ isEditingEnterprise ? '保存' : '创建' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 添加/编辑材料对话框 -->
    <el-dialog v-model="showMaterialDialog" :title="isEditingMaterial ? '编辑材料' : '添加材料'" width="600px" :close-on-click-modal="false">
      <el-form ref="materialFormRef" :model="materialForm" :rules="materialRules" label-width="100px">
        <el-form-item label="材料类型" prop="material_type">
          <el-select v-model="materialForm.material_type" style="width: 100%">
            <el-option label="资质证书" value="QUALIFICATION" />
            <el-option label="证书" value="CERTIFICATE" />
            <el-option label="业绩" value="PROJECT_EXPERIENCE" />
            <el-option label="人员" value="PERSONNEL" />
          </el-select>
        </el-form-item>
        <el-form-item label="材料名称" prop="name">
          <el-input v-model="materialForm.name" placeholder="请输入材料名称" />
        </el-form-item>
        <el-form-item label="证书编号">
          <el-input v-model="materialForm.material_no" placeholder="请输入证书编号" />
        </el-form-item>
        <el-form-item label="等级">
          <el-input v-model="materialForm.level" placeholder="如：一级、二级" />
        </el-form-item>
        <el-form-item label="发证机关">
          <el-input v-model="materialForm.issuer" placeholder="请输入发证机关" />
        </el-form-item>
        <el-form-item label="有效期">
          <el-date-picker v-model="materialForm.validity" type="daterange" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" style="width: 100%" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showMaterialDialog = false">取消</el-button>
        <el-button type="primary" :loading="savingMaterial" @click="handleSaveMaterial">
          {{ isEditingMaterial ? '保存' : '添加' }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Loading, OfficeBuilding } from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import type { FormInstance, FormRules } from 'element-plus'
import { enterpriseApi, materialApi } from '@/api'
import type { Enterprise, EnterpriseMaterial } from '@/types'

const loading = ref(false)
const enterprises = ref<Enterprise[]>([])
const allMaterials = ref<EnterpriseMaterial[]>([])
const expandedIds = ref(new Set<string>())
const detailLoading = ref(new Set<string>())
const activeMaterialType = reactive<Record<string, string>>({})

const showEnterpriseDialog = ref(false)
const savingEnterprise = ref(false)
const isEditingEnterprise = ref(false)
const currentEnterpriseId = ref<string | null>(null)
const enterpriseFormRef = ref<FormInstance>()

const showMaterialDialog = ref(false)
const savingMaterial = ref(false)
const isEditingMaterial = ref(false)
const currentMaterialId = ref<string | null>(null)
const currentMaterialEnterpriseId = ref<string | null>(null)
const materialFormRef = ref<FormInstance>()

const materialTypes = [
  { label: '全部', value: '' },
  { label: '资质', value: 'QUALIFICATION' },
  { label: '证书', value: 'CERTIFICATE' },
  { label: '业绩', value: 'PROJECT_EXPERIENCE' },
  { label: '人员', value: 'PERSONNEL' },
]

const enterpriseForm = reactive({
  name: '',
  credit_code: '',
  enterprise_type: '',
})

const enterpriseRules: FormRules = {
  name: [{ required: true, message: '请输入企业名称', trigger: 'blur' }],
  enterprise_type: [{ required: true, message: '请选择企业类型', trigger: 'change' }],
}

const materialForm = reactive({
  material_type: '',
  name: '',
  material_no: '',
  level: '',
  issuer: '',
  validity: null as [Date, Date] | null,
})

const materialRules: FormRules = {
  material_type: [{ required: true, message: '请选择材料类型', trigger: 'change' }],
  name: [{ required: true, message: '请输入材料名称', trigger: 'blur' }],
}

// 计算每个企业id对应的材料
const enterpriseMaterials = computed(() => {
  const map: Record<string, EnterpriseMaterial[]> = {}
  for (const e of enterprises.value) {
    map[e.id] = allMaterials.value.filter(m => m.enterprise_id === e.id)
  }
  return map
})

import { computed } from 'vue'

const fetchEnterprises = async () => {
  loading.value = true
  try {
    enterprises.value = await enterpriseApi.list()
  } finally {
    loading.value = false
  }
}

const fetchAllMaterials = async () => {
  allMaterials.value = await materialApi.list()
}

const onEnterpriseExpand = async (row: any) => {
  const enterprise = row as Enterprise
  if (expandedIds.value.has(enterprise.id)) {
    expandedIds.value.delete(enterprise.id)
    return
  }
  expandedIds.value.add(enterprise.id)
  if (activeMaterialType[enterprise.id] === undefined) {
    activeMaterialType[enterprise.id] = ''
  }
}

const getFilteredMaterials = (enterpriseId: string) => {
  const mats = enterpriseMaterials.value[enterpriseId] || []
  const type = activeMaterialType[enterpriseId]
  if (!type) return mats
  return mats.filter(m => m.material_type === type)
}

const getTypeCount = (enterpriseId: string, type: string) => {
  const mats = enterpriseMaterials.value[enterpriseId] || []
  if (!type) return mats.length
  return mats.filter(m => m.material_type === type).length
}

const openCreateEnterpriseDialog = () => {
  isEditingEnterprise.value = false
  currentEnterpriseId.value = null
  enterpriseForm.name = ''
  enterpriseForm.credit_code = ''
  enterpriseForm.enterprise_type = ''
  showEnterpriseDialog.value = true
}

const openEditEnterpriseDialog = (enterprise: Enterprise) => {
  isEditingEnterprise.value = true
  currentEnterpriseId.value = enterprise.id
  enterpriseForm.name = enterprise.name
  enterpriseForm.credit_code = enterprise.credit_code || ''
  enterpriseForm.enterprise_type = enterprise.enterprise_type || ''
  showEnterpriseDialog.value = true
}

const handleSaveEnterprise = async () => {
  if (!enterpriseFormRef.value) return
  try {
    await enterpriseFormRef.value.validate()
    savingEnterprise.value = true
    if (isEditingEnterprise.value && currentEnterpriseId.value) {
      await enterpriseApi.update(currentEnterpriseId.value, enterpriseForm)
      ElMessage.success('企业已更新')
    } else {
      await enterpriseApi.create(enterpriseForm)
      ElMessage.success('企业已创建')
    }
    showEnterpriseDialog.value = false
    fetchEnterprises()
  } catch (e: any) {
    if (e?.message) ElMessage.error(e.message)
  } finally {
    savingEnterprise.value = false
  }
}

const openAddMaterialDialog = (enterpriseId: string) => {
  isEditingMaterial.value = false
  currentMaterialId.value = null
  currentMaterialEnterpriseId.value = enterpriseId
  materialForm.material_type = ''
  materialForm.name = ''
  materialForm.material_no = ''
  materialForm.level = ''
  materialForm.issuer = ''
  materialForm.validity = null
  showMaterialDialog.value = true
}

const openEditMaterialDialog = (material: EnterpriseMaterial) => {
  isEditingMaterial.value = true
  currentMaterialId.value = material.id
  currentMaterialEnterpriseId.value = material.enterprise_id || null
  materialForm.material_type = material.material_type
  materialForm.name = material.name
  materialForm.material_no = material.material_no || ''
  materialForm.level = material.level || ''
  materialForm.issuer = material.issuer || ''
  materialForm.validity = material.valid_from && material.valid_to
    ? [new Date(material.valid_from), new Date(material.valid_to)]
    : null
  showMaterialDialog.value = true
}

const handleSaveMaterial = async () => {
  if (!materialFormRef.value) return
  try {
    await materialFormRef.value.validate()
    savingMaterial.value = true
    const data: any = {
      material_type: materialForm.material_type,
      name: materialForm.name,
      material_no: materialForm.material_no || undefined,
      level: materialForm.level || undefined,
      issuer: materialForm.issuer || undefined,
      valid_from: materialForm.validity?.[0]?.toISOString() || null,
      valid_to: materialForm.validity?.[1]?.toISOString() || null,
      self_declared: true,
    }
    if (isEditingMaterial.value && currentMaterialId.value) {
      await materialApi.update(currentMaterialId.value, data)
      ElMessage.success('材料已更新')
    } else if (currentMaterialEnterpriseId.value) {
      await materialApi.create({ ...data, enterprise_id: currentMaterialEnterpriseId.value })
      ElMessage.success('材料已添加')
    }
    showMaterialDialog.value = false
    await fetchAllMaterials()
  } catch (e: any) {
    if (e?.message) ElMessage.error(e.message)
  } finally {
    savingMaterial.value = false
  }
}

const handleDeleteMaterial = async (material: EnterpriseMaterial) => {
  try {
    await ElMessageBox.confirm(`确定删除材料「${material.name}」吗？`, '删除确认', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await materialApi.delete(material.id)
    ElMessage.success('材料已删除')
    await fetchAllMaterials()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

const getEnterpriseTypeText = (type: string | null) => ({
  PARENT: '母公司', SUBSIDIARY: '子公司', INDEPENDENT: '独立公司'
}[type || ''] || type || '-')

const getMaterialTypeText = (type: string) => ({
  QUALIFICATION: '资质', CERTIFICATE: '证书', PROJECT_EXPERIENCE: '业绩', PERSONNEL: '人员'
}[type] || type)

const formatDate = (d?: string | null) => d ? dayjs(d).format('YYYY-MM-DD') : '-'
const isExpired = (d?: string | null) => d ? dayjs(d).isBefore(dayjs()) : false

onMounted(() => {
  fetchEnterprises()
  fetchAllMaterials()
})
</script>

<style scoped>
.material-library {
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

.loading-container {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-2);
  padding: var(--spacing-16);
  color: var(--color-text-muted);
}

.enterprise-table {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

/* 展开行样式 */
.enterprise-detail {
  padding: var(--spacing-4) var(--spacing-6);
  background: var(--color-background);
}

.detail-section {
  margin-bottom: var(--spacing-6);
}

.detail-section:last-child {
  margin-bottom: 0;
}

.detail-title {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-secondary);
  margin: 0 0 var(--spacing-3) 0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: var(--spacing-3);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.info-value {
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
}

.section-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  margin-bottom: var(--spacing-3);
}

.type-tabs {
  display: flex;
  gap: var(--spacing-1);
}

.type-tab {
  font-size: var(--font-size-sm);
  padding: var(--spacing-1) var(--spacing-3);
  border-radius: var(--radius-full);
  cursor: pointer;
  color: var(--color-text-secondary);
  background: transparent;
  transition: all var(--transition-fast);
  display: flex;
  align-items: center;
  gap: 4px;
}

.type-tab:hover {
  background: var(--color-surface);
}

.type-tab.active {
  background: var(--color-primary);
  color: white;
}

.type-count {
  font-size: var(--font-size-xs);
  background: rgba(255,255,255,0.3);
  padding: 0 6px;
  border-radius: 10px;
  min-width: 20px;
  text-align: center;
}

.type-tab:not(.active) .type-count {
  background: var(--color-border);
}

.materials-list {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.material-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
  padding: var(--spacing-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.material-info {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  flex-wrap: wrap;
}

.material-name {
  font-weight: 600;
  color: var(--color-text-primary);
}

.material-level,
.material-no {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.material-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
  font-size: var(--font-size-sm);
}

.validity {
  color: var(--color-text-secondary);
}

.validity.expired {
  color: var(--color-destructive);
}

.no-expiry {
  color: var(--color-text-muted);
}

.material-actions {
  display: flex;
  gap: var(--spacing-1);
}

.empty-materials {
  padding: var(--spacing-6);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--font-size-sm);
  background: var(--color-surface);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
}

.add-material-bar {
  margin-top: var(--spacing-3);
}

.credit-code {
  font-family: monospace;
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.type-tag {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: var(--color-background);
  border-radius: var(--radius-sm);
  color: var(--color-text-secondary);
}

.type-badge {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

.type-qualification { background: #E0E7FF; color: #3730A3; }
.type-certificate { background: #D1FAE5; color: #065F46; }
.type-project_experience { background: #FEF3C7; color: #92400E; }
.type-personnel { background: #FCE7F3; color: #9D174D; }

.status-badge {
  font-size: var(--font-size-xs);
  padding: 2px 8px;
  border-radius: var(--radius-full);
}

.status-badge.active {
  background: #D1FAE5;
  color: #065F46;
}

.status-badge.inactive {
  background: #FEE2E2;
  color: #991B1B;
}

.expired-tag {
  font-size: var(--font-size-xs);
  color: var(--color-destructive);
  margin-left: 4px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: var(--spacing-16);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
}

.empty-state-icon {
  font-size: 48px;
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-4);
}

.empty-state-title {
  font-size: var(--font-size-lg);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0 0 var(--spacing-2) 0;
}

.empty-state-description {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin: 0 0 var(--spacing-6) 0;
}
</style>
