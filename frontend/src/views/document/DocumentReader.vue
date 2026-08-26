<template>
  <div class="document-reader">
    <!-- 顶部工具栏 -->
    <div class="reader-toolbar">
      <div class="toolbar-left">
        <el-button text :icon="ArrowLeft" @click="goBack">
          返回
        </el-button>
        <div class="document-info">
          <span class="doc-title">{{ document?.versions?.[0]?.file_name || document?.logical_name }}</span>
          <span :class="['badge', `badge-${getDocStatusClass(document?.versions?.[0]?.parse_status)}`]">
            {{ getDocStatusText(document?.versions?.[0]?.parse_status) }}
          </span>
        </div>
      </div>

      <div class="toolbar-center">
        <el-button-group>
          <el-button :type="activePanel === 'outline' ? 'primary' : 'default'" @click="activePanel = 'outline'">
            目录
          </el-button>
          <el-button :type="activePanel === 'context' ? 'primary' : 'default'" @click="activePanel = 'context'">
            上下文
          </el-button>
        </el-button-group>
      </div>

      <div class="toolbar-right">
        <el-button :icon="ZoomOut" :disabled="scale <= 0.5" @click="scale -= 0.1" />
        <span class="scale-text">{{ Math.round(scale * 100) }}%</span>
        <el-button :icon="ZoomIn" :disabled="scale >= 2" @click="scale += 0.1" />
        <el-button :icon="Download" @click="handleDownload">原文</el-button>
      </div>
    </div>

    <!-- 三栏布局 -->
    <div class="reader-content">
      <!-- 左侧目录 -->
      <aside v-show="activePanel === 'outline'" class="outline-panel">
        <div class="panel-header">
          <h3>文档目录</h3>
        </div>
        <div class="outline-tree">
          <div
            v-for="node in outlineNodes"
            :key="node.id"
            :class="['outline-item', { active: currentNode?.id === node.id }]"
            :style="{ paddingLeft: `${(node.level - 1) * 16 + 12}px` }"
            @click="selectNode(node)"
          >
            <el-icon v-if="node.node_type === 'SECTION'" class="outline-icon"><Folder /></el-icon>
            <el-icon v-else-if="node.node_type === 'TABLE'" class="outline-icon"><Grid /></el-icon>
            <el-icon v-else-if="node.node_type === 'IMAGE'" class="outline-icon"><Picture /></el-icon>
            <el-icon v-else class="outline-icon"><Document /></el-icon>
            <span class="outline-text">{{ node.title || node.content?.substring(0, 50) }}</span>
            <span class="outline-page">{{ node.page_number }}</span>
          </div>
        </div>
      </aside>

      <!-- 中间文档内容 -->
      <main class="document-content" ref="contentRef">
        <div class="content-wrapper" :style="{ transform: `scale(${scale})`, transformOrigin: 'top center' }">
          <div v-if="loading" class="loading-state">
            <el-icon class="is-loading" :size="32"><Loading /></el-icon>
            <span>加载文档内容...</span>
          </div>

          <div v-else-if="currentNode" class="node-content">
            <div class="node-header">
              <span class="node-type">{{ getNodeTypeText(currentNode.node_type) }}</span>
              <span class="node-page">第 {{ currentNode.page_number }} 页</span>
              <span v-if="currentNode.section_path" class="node-path">{{ currentNode.section_path }}</span>
            </div>

            <div v-if="currentNode.node_type === 'TABLE'" class="table-content">
              <p class="table-note">表格内容（简化展示）</p>
              <div class="table-placeholder">
                <el-icon :size="48"><Grid /></el-icon>
                <span>表格详情</span>
              </div>
            </div>

            <div v-else-if="currentNode.node_type === 'IMAGE'" class="image-content">
              <div class="image-placeholder">
                <el-icon :size="48"><Picture /></el-icon>
                <span>图片内容</span>
              </div>
            </div>

            <div v-else class="text-content">
              {{ currentNode.content }}
            </div>

            <div v-if="currentNode.evidence" class="evidence-section">
              <h4>引用证据</h4>
              <div class="evidence-card" @click="showEvidenceDetail(currentNode.evidence)">
                <p class="evidence-text">"{{ currentNode.evidence.quoted_text }}"</p>
                <span class="evidence-meta">
                  来源：{{ currentNode.evidence.source_type }}
                </span>
              </div>
            </div>
          </div>

          <div v-else class="empty-state">
            <el-icon :size="64"><Document /></el-icon>
            <h3 v-if="!isParsed">文档解析中，请稍候</h3>
            <p v-if="!isParsed">结构化数据加载完成后可浏览目录和节点</p>
            <h3 v-else>选择节点查看内容</h3>
            <p v-if="isParsed">点击左侧目录或使用键盘上下键浏览文档</p>
          </div>
        </div>
      </main>

      <!-- 右侧上下文面板 -->
      <aside v-show="activePanel === 'context'" class="context-panel">
        <div class="panel-header">
          <h3>上下文信息</h3>
        </div>

        <div v-if="contextItems.length" class="context-items">
          <div
            v-for="item in contextItems"
            :key="item.id"
            :class="['context-item', { active: item.type === activeContextType }]"
            @click="activeContextType = item.type"
          >
            <div class="context-header">
              <span class="context-type">{{ getContextTypeText(item.type) }}</span>
              <el-icon v-if="item.status === 'pending'" class="is-loading"><Loading /></el-icon>
            </div>
            <p class="context-title">{{ item.title }}</p>
            <p v-if="item.description" class="context-desc">{{ item.description }}</p>
          </div>
        </div>

        <div v-else class="context-empty">
          <p>暂无相关信息</p>
        </div>
      </aside>

      <!-- 右侧上下文 - 默认显示 -->
      <aside v-if="activePanel === 'outline'" class="context-panel default-context">
        <div class="panel-header">
          <h3>节点信息</h3>
        </div>

        <div v-if="currentNode" class="node-info">
          <div class="info-item">
            <label>节点类型</label>
            <span>{{ getNodeTypeText(currentNode.node_type) }}</span>
          </div>
          <div class="info-item">
            <label>页码</label>
            <span>{{ currentNode.page_number }}</span>
          </div>
          <div class="info-item">
            <label>顺序号</label>
            <span>{{ currentNode.order_no }}</span>
          </div>
          <div v-if="currentNode.section_path" class="info-item">
            <label>章节路径</label>
            <span class="section-path">{{ currentNode.section_path }}</span>
          </div>

          <div class="info-actions">
            <el-button size="small" @click="copyContent">复制内容</el-button>
            <el-button size="small" @click="viewEvidence">查看证据</el-button>
          </div>
        </div>

        <div v-else class="context-empty">
          <p>选择一个节点查看详情</p>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { documentApi } from '@/api'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft, ZoomIn, ZoomOut, Folder, Grid, Picture, Document, Loading, Download
} from '@element-plus/icons-vue'
import type { DocumentNode, Evidence } from '@/types'

const router = useRouter()
const route = useRoute()

const projectId = route.params.projectId as string
const documentId = route.params.documentId as string

const document = ref<any>(null)
const nodes = ref<DocumentNode[]>([])
const currentNode = ref<(DocumentNode & { evidence?: Evidence }) | null>(null)
const loading = ref(false)
const scale = ref(1)
const activePanel = ref<'outline' | 'context'>('outline')
const contentRef = ref<HTMLElement>()

interface OutlineNode extends DocumentNode {
  level: number
  title: string
}

interface ContextItem {
  id: string
  type: 'requirement' | 'risk' | 'match' | 'evidence'
  title: string
  description?: string
  status: 'pending' | 'ready'
}

const outlineNodes = computed<OutlineNode[]>(() => {
  return nodes.value.map(node => {
    const level = node.section_path?.split('/').length || 1
    const title = node.node_type === 'SECTION'
      ? node.content?.substring(0, 100)
      : node.content?.substring(0, 50) + (node.content.length > 50 ? '...' : '')
    return { ...node, level, title: title || '' }
  })
})

const contextItems = ref<ContextItem[]>([])
const activeContextType = ref<string>('')

const isParsed = computed(() => {
  const status = document.value?.versions?.[0]?.parse_status
  return status === 'READY' || status === 'PARSED'
})

const goBack = () => {
  router.push(`/projects/${projectId}`)
}

const selectNode = (node: DocumentNode) => {
  currentNode.value = node
  // 滚动到视图中部
  setTimeout(() => {
    contentRef.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, 100)
}

const getDocStatusClass = (status?: string) => {
  const map: Record<string, string> = { READY: 'active', FAILED: 'failed', PARSING: 'draft' }
  return map[status || ''] || 'draft'
}

const getDocStatusText = (status?: string) => {
  const map: Record<string, string> = {
    UPLOADED: '已上传', QUEUED: '排队中', PARSING: '解析中', PARSED: '已解析',
    STRUCTURING: '结构化', INDEXING: '索引中', READY: '就绪', FAILED: '失败'
  }
  return map[status || ''] || status
}

const getNodeTypeText = (type: string) => {
  const map: Record<string, string> = {
    SECTION: '章节', PARAGRAPH: '段落', LIST: '列表',
    TABLE: '表格', CELL: '单元格', IMAGE: '图片'
  }
  return map[type] || type
}

const getContextTypeText = (type: string) => {
  const map: Record<string, string> = {
    requirement: '需求', risk: '风险', match: '匹配', evidence: '证据'
  }
  return map[type] || type
}

const copyContent = () => {
  if (currentNode.value?.content) {
    navigator.clipboard.writeText(currentNode.value.content)
    ElMessage.success('内容已复制')
  }
}

const viewEvidence = () => {
  if (currentNode.value?.evidence) {
    showEvidenceDetail(currentNode.value.evidence)
  }
}

const handleDownload = async () => {
  try {
    const { url } = await documentApi.downloadUrl(documentId)
    window.open(url, '_blank')
  } catch {
    ElMessage.error('获取下载链接失败')
  }
}

const showEvidenceDetail = (evidence: Evidence) => {
  ElMessage.info('查看证据详情：' + evidence.id)
}

onMounted(async () => {
  loading.value = true
  try {
    document.value = await documentApi.get(documentId)
    // 后端单页上限为 200；逐页读取，避免长招标文件被静默截断。
    const pageSize = 200
    let offset = 0
    const allNodes: DocumentNode[] = []
    while (true) {
      const response = await documentApi.getNodes(documentId, { offset, limit: pageSize })
      const pageNodes = response?.items ?? []
      allNodes.push(...pageNodes)
      if (pageNodes.length < pageSize) break
      offset += pageNodes.length
    }
    nodes.value = allNodes
    if (nodes.value.length > 0) {
      currentNode.value = nodes.value[0] as DocumentNode & { evidence?: Evidence }
    }
  } catch (error) {
    ElMessage.error('加载文档失败')
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.document-reader {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 64px);
  margin: calc(-1 * var(--spacing-6));
  background: var(--color-background);
}

.reader-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--spacing-3) var(--spacing-4);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
}

.document-info {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.doc-title {
  font-weight: 600;
  color: var(--color-text-primary);
}

.toolbar-center {
  display: flex;
  align-items: center;
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.scale-text {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  min-width: 48px;
  text-align: center;
}

.reader-content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 左侧目录 */
.outline-panel {
  width: 280px;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.panel-header {
  padding: var(--spacing-4);
  border-bottom: 1px solid var(--color-border);
}

.panel-header h3 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.outline-tree {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-2) 0;
}

.outline-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-2) var(--spacing-3);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.outline-item:hover {
  background: var(--color-background);
}

.outline-item.active {
  background: #EFF6FF;
  border-right: 2px solid var(--color-accent);
}

.outline-icon {
  font-size: 14px;
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.outline-text {
  flex: 1;
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.outline-page {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

/* 中间内容 */
.document-content {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-8);
  display: flex;
  justify-content: center;
}

.content-wrapper {
  width: 100%;
  max-width: 800px;
  transition: transform var(--transition-fast);
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-3);
  padding: var(--spacing-16);
  color: var(--color-text-muted);
}

.node-content {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--spacing-6);
}

.node-header {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
  padding-bottom: var(--spacing-4);
  margin-bottom: var(--spacing-4);
  border-bottom: 1px solid var(--color-border);
}

.node-type {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: var(--color-accent);
  color: white;
  border-radius: var(--radius-sm);
}

.node-page {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
}

.node-path {
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
}

.text-content {
  font-size: var(--font-size-base);
  line-height: 1.8;
  color: var(--color-text-primary);
  white-space: pre-wrap;
}

.table-content, .image-content {
  text-align: center;
  padding: var(--spacing-8);
}

.table-note, .image-placeholder span {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-4);
}

.table-placeholder, .image-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--spacing-3);
  padding: var(--spacing-12);
  background: var(--color-background);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
  color: var(--color-text-muted);
}

.evidence-section {
  margin-top: var(--spacing-6);
  padding-top: var(--spacing-4);
  border-top: 1px solid var(--color-border);
}

.evidence-section h4 {
  font-size: var(--font-size-sm);
  font-weight: 600;
  margin-bottom: var(--spacing-3);
}

.evidence-card {
  padding: var(--spacing-3);
  background: var(--color-background);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.evidence-card:hover {
  background: var(--color-border);
}

.evidence-text {
  font-size: var(--font-size-sm);
  font-style: italic;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-2);
}

.evidence-meta {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

/* 右侧上下文 */
.context-panel {
  width: 320px;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.context-items {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-2);
}

.context-item {
  padding: var(--spacing-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background-color var(--transition-fast);
}

.context-item:hover {
  background: var(--color-background);
}

.context-item.active {
  background: #EFF6FF;
}

.context-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--spacing-2);
}

.context-type {
  font-size: var(--font-size-xs);
  padding: var(--spacing-1) var(--spacing-2);
  background: var(--color-accent);
  color: white;
  border-radius: var(--radius-sm);
}

.context-title {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-primary);
  margin-bottom: var(--spacing-1);
}

.context-desc {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.context-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--color-text-muted);
}

/* 节点信息 */
.node-info {
  padding: var(--spacing-4);
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-1);
  margin-bottom: var(--spacing-4);
}

.info-item label {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.info-item span {
  font-size: var(--font-size-sm);
  color: var(--color-text-primary);
}

.section-path {
  font-family: monospace;
  font-size: var(--font-size-xs);
}

.info-actions {
  display: flex;
  gap: var(--spacing-2);
  margin-top: var(--spacing-4);
}
</style>
