<template>
  <div class="chat-layout">
    <!-- 左侧边栏 -->
    <aside class="chat-sidebar">
      <div class="sidebar-logo">
        <span class="logo-text">BidWise</span>
      </div>

      <nav class="sidebar-nav">
        <el-button type="primary" :icon="Plus" class="new-chat-btn" @click="createNewChat">
          新建会话
        </el-button>
      </nav>

      <div class="sidebar-history">
        <div class="history-label">今天</div>
        <div
          v-for="session in sessions"
          :key="session.id"
          class="history-item"
          :class="{ active: currentSessionId === session.id }"
          @click="switchSession(session.id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="history-title">{{ session.title || '新会话' }}</span>
          <el-button
            :icon="Delete"
            text
            size="small"
            class="history-delete"
            @click.stop="confirmDelete(session.id)"
          />
        </div>
        <div v-if="sessions.length === 0" class="history-empty">暂无对话</div>
      </div>
    </aside>

    <!-- 主内容区 -->
    <main class="chat-main">
      <ChatMessageList
        :messages="messages"
        :empty-title="'智能助手'"
        :empty-subtitle="'项目文件、法律知识、风险与报告均可查询，并提供原文依据'"
        :empty-prompts="quickPrompts"
        @select-prompt="handleSelectPrompt"
        @regenerate="handleRegenerate"
      />

      <div class="input-area">
        <ChatInput
          v-model="inputText"
          :is-streaming="isStreaming"
          @send="handleSend"
          @stop="stopGeneration"
          @attach="openFilePicker"
        />
        <input
          ref="fileInputRef"
          class="file-input"
          type="file"
          accept=".pdf,.doc,.docx,.txt,.md"
          @change="handleFileSelected"
        />
      </div>
    </main>
    <el-dialog v-model="showProjectPicker" title="请选择关联项目" width="480px">
      <p>该问题需要查询项目资料，请选择项目后继续。</p>
      <el-radio-group v-model="pendingProjectId" class="project-picker">
        <el-radio v-for="project in projects" :key="project.id" :value="project.id || ''">{{ project.name }}</el-radio>
      </el-radio-group>
      <template #footer><el-button @click="showProjectPicker = false">取消</el-button><el-button type="primary" :disabled="!pendingProjectId" @click="confirmProjectAndSend">继续提问</el-button></template>
    </el-dialog>

    <el-dialog v-model="showUploadDialog" title="上传文件" width="520px" :close-on-click-modal="!uploading">
      <el-alert type="info" :closable="false" show-icon>
        <template #title>文件将进入对应的既有处理链路，不会在聊天中另存一份。</template>
      </el-alert>
      <div class="upload-file-name">
        <el-icon><Paperclip /></el-icon>
        <span>{{ selectedFile?.name }}</span>
      </div>
      <p class="upload-target-title">请选择文件用途</p>
      <div class="upload-targets">
        <button type="button" class="upload-target" :class="{ selected: uploadTarget === 'TENDER' }" :disabled="uploading" @click="uploadTarget = 'TENDER'">
          <strong>招标文件</strong><small>新建项目后进入 MinerU 解析与统一分析链路</small>
        </button>
        <button type="button" class="upload-target" :class="{ selected: uploadTarget === 'LEGAL' }" :disabled="uploading" @click="uploadTarget = 'LEGAL'">
          <strong>法律 / 规范文件</strong><small>上传至法律知识库，供问答检索使用</small>
        </button>
      </div>
      <el-alert v-if="uploadTarget === 'TENDER'" type="info" :closable="false" show-icon>
        下一步将打开“新建项目”表单。提交后自动上传该文件并启动解析。
      </el-alert>
      <template #footer>
        <el-button :disabled="uploading" @click="showUploadDialog = false">取消</el-button>
        <el-button :loading="uploading" type="primary" @click="submitFileUpload">上传并处理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound, Plus, Delete, Paperclip
} from '@element-plus/icons-vue'
import { useChatStream } from '@/composables/useChatStream'
import { usePendingTenderUpload } from '@/composables/usePendingTenderUpload'
import { useRouter } from 'vue-router'
import { projectApi, chatApi } from '@/api'
import type { ChatMessage, Project } from '@/types'

const inputText = ref('')
const router = useRouter()
const { setPendingTenderFile, setPendingLegalFile } = usePendingTenderUpload()
const messages = reactive<ChatMessage[]>([])
const currentCitations = ref<{ evidence_id: string; content?: string }[]>([])

// 快捷问题
const quickPrompts = [
  '投标保函的有效期是多久？',
  '招标文件中哪些条款需要重点关注？',
  '中标后如何进行合同签订？',
]

// 单一会话助手：后端统一决定法律、项目文件或报告检索路径。
const projects = ref<Project[]>([])
const selectedProjectId = ref<string | null>(null)
const showProjectPicker = ref(false)
const pendingProjectId = ref('')
const pendingQuestion = ref('')
const fileInputRef = ref<HTMLInputElement>()
const selectedFile = ref<File | null>(null)
const showUploadDialog = ref(false)
const uploadTarget = ref<'TENDER' | 'LEGAL' | null>(null)
const uploading = ref(false)

// 会话相关
const sessions = ref<{ id: string; title: string; updated_at: string }[]>([])
const currentSessionId = ref<string | null>(null)


// 流式处理
const { startStream, stopStream, isStreaming } = useChatStream({
  onDelta: (text) => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content += text
    }
  },
  onDone: (answer, citations) => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content = answer
      lastMsg.citations = citations
      lastMsg.streaming = false
      lastMsg.status = 'ok'
      currentCitations.value = citations || []
      loadSessions()
    }
  },
  onError: (msg) => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content = msg
      lastMsg.streaming = false
      lastMsg.status = 'error'
    }
  },
})

// 选择智能体
// 加载项目列表（投标分析助手需要项目上下文）
async function loadProjects() {
  try {
    projects.value = await projectApi.list()
  } catch {
    // 拦截器已提示
  }
}

function openFilePicker() {
  if (isStreaming.value) return
  fileInputRef.value?.click()
}

function handleFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  selectedFile.value = file
  uploadTarget.value = null
  showUploadDialog.value = true
}

async function submitFileUpload() {
  if (!selectedFile.value) return
  if (!uploadTarget.value) {
    ElMessage.warning('请选择文件用途')
    return
  }
  uploading.value = true
  try {
    if (uploadTarget.value === 'TENDER') {
      setPendingTenderFile(selectedFile.value)
      showUploadDialog.value = false
      selectedFile.value = null
      router.push({ path: '/projects', query: { create: 'tender' } })
      return
    } else {
      setPendingLegalFile(selectedFile.value)
      showUploadDialog.value = false
      selectedFile.value = null
      router.push({ path: '/knowledge', query: { import: 'legal' } })
      return
    }
    showUploadDialog.value = false
    selectedFile.value = null
  } catch (error: any) {
    ElMessage.error(error?.message || '文件上传失败，请稍后重试')
  } finally {
    uploading.value = false
  }
}

// 创建新会话
async function createNewChat() {
  try {
    const result = await chatApi.createSession({ title: '新对话' })
    sessions.value.unshift({
      id: result.id,
      title: result.title || '新对话',
      updated_at: result.updated_at,
    })
    currentSessionId.value = result.id
    messages.splice(0)
    currentCitations.value = []
    inputText.value = ''
  } catch (e: any) {
    ElMessage.error(e?.message || '创建会话失败')
  }
}

async function ensureCurrentSession(projectId?: string) {
  if (currentSessionId.value) return currentSessionId.value
  const result = await chatApi.createSession({ title: '新对话', project_id: projectId })
  sessions.value.unshift({
    id: result.id,
    title: result.title || '新对话',
    updated_at: result.updated_at,
  })
  currentSessionId.value = result.id
  return result.id
}

// 删除会话确认
async function confirmDelete(sessionId: string) {
  try {
    await ElMessageBox.confirm('确定要删除该会话吗？删除后无法恢复。', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await chatApi.deleteSession(sessionId)
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
    if (currentSessionId.value === sessionId) {
      currentSessionId.value = null
      messages.splice(0)
    }
    ElMessage.success('会话已删除')
  } catch {
    // cancel
  }
}

// 切换会话
async function switchSession(id: string) {
  currentSessionId.value = id
  messages.splice(0)
  currentCitations.value = []
  try {
    const result = await chatApi.getMessages(id)
    messages.push(...result.items)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载消息失败')
  }
}

// 加载会话列表
async function loadSessions() {
  try {
    const result = await chatApi.getSessions(1, 50)
    sessions.value = result.items.map(s => ({
      id: s.id,
      title: s.title || '新对话',
      updated_at: s.updated_at,
    }))
  } catch (e: any) {
    console.error('loadSessions failed:', e?.response?.data || e?.message || e)
  }
}

// 选择快捷问题
function handleSelectPrompt(prompt: string) {
  inputText.value = prompt
}

// 发送消息
async function handleSend(text: string) {
  if (!text.trim() || isStreaming.value) return
  if (!selectedProjectId.value && projects.value.length > 1 && /项目|招标|报告|风险|资质|文件/.test(text)) {
    pendingQuestion.value = text
    showProjectPicker.value = true
    return
  }

  // 确保有会话
  if (!currentSessionId.value) {
    try {
      await ensureCurrentSession(selectedProjectId.value || undefined)
    } catch {
      // continue with temp id
    }
  }

  stopStream()
  currentCitations.value = []

  // 用户消息
  const userMsg: ChatMessage = {
    id: crypto.randomUUID(),
    role: 'user',
    content: text,
    created_at: new Date().toISOString(),
  }
  messages.push(userMsg)

  // 助手消息占位
  const assistantMsg: ChatMessage = {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: '',
    streaming: true,
    status: 'streaming',
    created_at: new Date().toISOString(),
  }
  messages.push(assistantMsg)

  await startStream(
    text,
    {
      projectId: selectedProjectId.value || undefined,
      sessionId: currentSessionId.value || undefined,
    },
  )
}

async function confirmProjectAndSend() {
  selectedProjectId.value = pendingProjectId.value
  showProjectPicker.value = false
  const project = projects.value.find(item => item.id === selectedProjectId.value)
  pendingQuestion.value = ''
  pendingProjectId.value = ''
  if (!project) return

  const confirmation = `### ✅ 已选择项目：${project.name}\n\n我已切换到该项目的招标文件、风险与报告上下文。\n\n💡 现在你可以直接问我：\n- **有哪些投标风险？**\n- **资格要求有哪些？**\n- **企业材料还缺什么？**`
  const message: ChatMessage = {
    id: crypto.randomUUID(),
    role: 'assistant',
    content: confirmation,
    created_at: new Date().toISOString(),
  }
  messages.push(message)
  try {
    const sessionId = await ensureCurrentSession(project.id)
    await chatApi.createMessage(sessionId, {
      role: 'assistant',
      content: confirmation,
    })
  } catch (error: any) {
    ElMessage.error(error?.message || '项目已选择，但提示消息暂未保存')
  }
}

// 停止生成
function stopGeneration() {
  stopStream()
}

// 重新生成
async function handleRegenerate(index: number) {
  const assistantMsg = messages[index]
  if (!assistantMsg || assistantMsg.role !== 'assistant') return

  // 找到上一条用户消息
  let userIndex = -1
  for (let i = index - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      userIndex = i
      break
    }
  }
  if (userIndex < 0) return

  const question = messages[userIndex].content
  assistantMsg.content = ''
  assistantMsg.streaming = true
  assistantMsg.status = 'streaming'
  assistantMsg.citations = []
  currentCitations.value = []

  await startStream(
    question,
    {
      projectId: selectedProjectId.value || undefined,
      sessionId: currentSessionId.value || undefined,
    },
  )
}

onMounted(() => {
  loadSessions()
  loadProjects()
})
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  background: var(--color-background);
}

/* 左侧边栏 */
.chat-sidebar {
  width: 260px;
  flex-shrink: 0;
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  box-shadow: 2px 0 8px rgba(0, 0, 0, 0.04);
}

.sidebar-logo {
  padding: var(--spacing-5) var(--spacing-5);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.logo-text {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: 0.02em;
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.sidebar-nav {
  padding: var(--spacing-3);
}

.new-chat-btn {
  width: 100%;
  margin-bottom: var(--spacing-2);
}

.sidebar-history {
  flex: 1;
  padding: var(--spacing-3);
  overflow-y: auto;
}

.history-label {
  padding: var(--spacing-2) var(--spacing-3) var(--spacing-1);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.history-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-2) var(--spacing-3);
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--color-text-secondary);
  font-size: var(--font-size-sm);
  transition: all var(--transition-fast);
  margin-bottom: 2px;
  position: relative;
}

.history-item:hover {
  background: var(--color-background);
  color: var(--color-text-primary);
}

.history-item.active {
  background: linear-gradient(135deg, rgba(3, 105, 161, 0.1), rgba(3, 105, 161, 0.05));
  color: var(--color-accent);
}

.history-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-delete {
  position: absolute;
  right: 8px;
  opacity: 0;
  transition: opacity var(--transition-fast);
}

.history-item:hover .history-delete {
  opacity: 1;
}

.history-empty {
  padding: var(--spacing-4);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  text-align: center;
}

/* 主内容区 */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  position: relative;
  min-width: 0;
  background: var(--color-background);
}

.input-area {
  padding: var(--spacing-4) var(--spacing-8) var(--spacing-5);
  background: linear-gradient(to top, var(--color-background) 80%, transparent);
}

.project-picker { display: flex; flex-direction: column; gap: var(--spacing-3); margin-top: var(--spacing-4); }
.file-input { display: none; }
.upload-file-name { display: flex; align-items: center; gap: var(--spacing-2); margin: var(--spacing-4) 0; color: var(--color-text-secondary); }
.upload-target-title { margin: var(--spacing-4) 0 var(--spacing-2); font-size: var(--font-size-sm); font-weight: 600; color: var(--color-text-primary); }
.upload-targets { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--spacing-3); margin-bottom: var(--spacing-4); }
.upload-target { padding: var(--spacing-3); text-align: left; border: 1px solid var(--color-border); border-radius: var(--radius-lg); background: var(--color-surface); cursor: pointer; transition: border-color var(--transition-fast), background var(--transition-fast); }
.upload-target:hover:not(:disabled), .upload-target.selected { border-color: var(--color-accent); background: rgba(3, 105, 161, 0.05); }
.upload-target:disabled { cursor: not-allowed; opacity: .65; }
.upload-target strong, .upload-target small { display: block; }
.upload-target small { margin-top: 4px; line-height: 1.5; color: var(--color-text-muted); }
.upload-form { margin-top: var(--spacing-4); }

@media (max-width: 1024px) {
  .input-area {
    padding: var(--spacing-3) var(--spacing-4) var(--spacing-4);
  }
}

@media (max-width: 768px) {
  .chat-sidebar {
    display: none;
  }

  .input-area {
    padding: var(--spacing-3);
  }
}
</style>
