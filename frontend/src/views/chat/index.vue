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
        <el-button text class="memory-btn" @click="openMemories">记忆管理</el-button>
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
      <div v-if="selectedProject" class="project-context-bar">
        <div class="project-context-icon">✓</div>
        <div class="project-context-copy">
          <span class="project-context-label">当前项目上下文</span>
          <strong>{{ selectedProject.name }}</strong>
          <span>将优先检索该项目已授权的招标文件与分析资料</span>
        </div>
        <el-button link type="primary" class="project-switch-btn" :disabled="isStreaming || isPreparingTurn" @click="openProjectPicker">更换</el-button>
      </div>
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
          :is-streaming="isStreaming || isPreparingTurn"
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
    <el-dialog v-model="showProjectPicker" title="选择项目上下文" width="480px">
      <p>{{ pendingQuestion ? '该问题需要查询项目资料，选择项目后将自动继续提问。' : '选择后，后续提问将优先检索该项目资料。' }}</p>
      <el-empty v-if="projects.length === 0" description="暂无可用项目，请先创建项目" :image-size="72" />
      <el-radio-group v-else v-model="pendingProjectId" class="project-picker">
        <el-radio v-for="project in projects" :key="project.id" :value="project.id || ''">{{ project.name }}</el-radio>
      </el-radio-group>
      <template #footer>
        <el-button @click="showProjectPicker = false">取消</el-button>
        <el-button v-if="projects.length === 0" type="primary" @click="goToProjectCreation">创建项目</el-button>
        <el-button v-else type="primary" :disabled="!pendingProjectId" @click="confirmProjectAndSend">{{ pendingQuestion ? '继续提问' : '确认选择' }}</el-button>
      </template>
    </el-dialog>
    <el-dialog v-model="showMemories" title="记忆管理" width="520px">
      <el-alert type="info" :closable="false" show-icon>系统会自动沉淀稳定偏好；你也可以在这里补充、修改或删除。记忆不作为法律或项目事实依据。</el-alert>
      <div class="memory-create"><el-input v-model.trim="memoryInput" placeholder="添加一条偏好（可选），例如：回答优先给结论和法条依据" @keyup.enter="saveMemory" /><el-button type="primary" :loading="savingMemory" @click="saveMemory">添加</el-button></div>
      <el-empty v-if="!memories.length" description="暂无长期记忆" :image-size="56" />
      <div v-for="item in memories" :key="item.id" class="memory-item"><div><el-tag size="small" :type="item.memory_type === 'PREFERENCE' ? 'success' : 'info'">{{ item.memory_type === 'PREFERENCE' ? '回答偏好' : '会话摘要' }}</el-tag><p>{{ item.content }}</p></div><el-button link type="danger" @click="removeMemory(item.id)">删除</el-button></div>
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
import { computed, ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ChatDotRound, Plus, Delete, Paperclip
} from '@element-plus/icons-vue'
import { useChatStream } from '@/composables/useChatStream'
import { usePendingTenderUpload } from '@/composables/usePendingTenderUpload'
import { useRouter } from 'vue-router'
import { projectApi, chatApi } from '@/api'
import type { ChatMessage, Citation, Project } from '@/types'

const inputText = ref('')
const router = useRouter()
const { setPendingTenderFile, setPendingLegalFile } = usePendingTenderUpload()
const messages = reactive<ChatMessage[]>([])
const currentCitations = ref<Citation[]>([])

// 快捷问题
const quickPrompts = [
  '投标保函的有效期是多久？',
  '招标文件中哪些条款需要重点关注？',
  '中标后如何进行合同签订？',
]

// 单一会话助手：后端统一决定法律、项目文件或报告检索路径。
const projects = ref<Project[]>([])
const projectsLoaded = ref(false)
let projectLoadPromise: Promise<void> | null = null
const selectedProjectId = ref<string | null>(null)
const selectedProject = computed(() => projects.value.find(project => project.id === selectedProjectId.value))
const showProjectPicker = ref(false)
const pendingProjectId = ref('')
const pendingQuestion = ref('')
const fileInputRef = ref<HTMLInputElement>()
const selectedFile = ref<File | null>(null)
const showUploadDialog = ref(false)
const uploadTarget = ref<'TENDER' | 'LEGAL' | null>(null)
const uploading = ref(false)
const showMemories = ref(false)
const memories = ref<any[]>([])
const memoryInput = ref('')
const savingMemory = ref(false)

// 会话相关
const sessions = ref<{ id: string; title: string; updated_at: string; project_id?: string | null }[]>([])
const currentSessionId = ref<string | null>(null)
const isPreparingTurn = ref(false)
let sessionLoadVersion = 0
let turnPreparationVersion = 0


// 流式处理
const { startStream, stopStream, isStreaming } = useChatStream({
  onDelta: (text) => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content += text
    }
  },
  onDone: (answer, citations, agentTrace, resolvedProjectId, resolvedSessionId) => {
    const lastMsg = messages[messages.length - 1]
    if (lastMsg && lastMsg.role === 'assistant') {
      lastMsg.content = answer
      lastMsg.citations = citations
      lastMsg.agent_trace = agentTrace
      lastMsg.streaming = false
      lastMsg.status = 'ok'
      currentCitations.value = citations || []
      if (resolvedProjectId) selectedProjectId.value = resolvedProjectId
      if (resolvedSessionId) currentSessionId.value = resolvedSessionId
      loadSessions()
    }
  },
  onError: (msg) => {
    if (msg.includes('项目') && msg.includes('选择')) {
      const question = messages[messages.length - 2]
      if (question?.role === 'user') pendingQuestion.value = question.content
      messages.splice(-2)
      pendingProjectId.value = ''
      showProjectPicker.value = true
      return
    }
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
  if (projectLoadPromise) return projectLoadPromise
  projectLoadPromise = (async () => {
    try {
      projects.value = await projectApi.list()
    } catch {
      // 拦截器已提示
    } finally {
      projectsLoaded.value = true
      projectLoadPromise = null
    }
  })()
  return projectLoadPromise
}

async function openProjectPicker() {
  if (isStreaming.value || isPreparingTurn.value) return
  if (!projectsLoaded.value) await loadProjects()
  if (projects.value.length === 0) {
    ElMessage.info('请先创建项目，再使用项目智能问答')
    router.push('/projects')
    return
  }
  pendingQuestion.value = ''
  pendingProjectId.value = selectedProjectId.value || ''
  showProjectPicker.value = true
}

function goToProjectCreation() {
  showProjectPicker.value = false
  router.push('/projects')
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
  sessionLoadVersion += 1
  stopGeneration()
  try {
    // “新建会话”始终从通用咨询开始，绝不继承当前项目的资料范围。
    selectedProjectId.value = null
    const result = await chatApi.createGlobalSession({ title: '新对话' })
    sessions.value.unshift({
      id: result.id,
      title: result.title || '新对话',
      updated_at: result.updated_at,
      project_id: null,
    })
    currentSessionId.value = result.id
    messages.splice(0)
    currentCitations.value = []
    inputText.value = ''
  } catch (e: any) {
    ElMessage.error(e?.message || '创建会话失败')
  }
}

async function openMemories() { showMemories.value = true; try { memories.value = await chatApi.listMemories() } catch (e: any) { ElMessage.error(e?.message || '加载长期记忆失败') } }
async function saveMemory() { if (!memoryInput.value) return; savingMemory.value = true; try { await chatApi.createMemory({ content: memoryInput.value, ...(selectedProjectId.value ? { project_id: selectedProjectId.value } : {}) }); memoryInput.value = ''; memories.value = await chatApi.listMemories(); ElMessage.success('长期记忆已保存') } catch (e: any) { ElMessage.error(e?.message || '保存失败') } finally { savingMemory.value = false } }
async function removeMemory(id: string) { try { await chatApi.deleteMemory(id); memories.value = memories.value.filter(item => item.id !== id); ElMessage.success('长期记忆已删除') } catch (e: any) { ElMessage.error(e?.message || '删除失败') } }

async function ensureCurrentSession(projectId?: string) {
  if (currentSessionId.value) return currentSessionId.value
  const result = projectId
    ? await chatApi.createSession(projectId, { title: '新对话' })
    : await chatApi.createGlobalSession({ title: '新对话' })
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
    const session = sessions.value.find(item => item.id === sessionId)
    if (session?.project_id) await chatApi.deleteSession(session.project_id, sessionId)
    else await chatApi.deleteGlobalSession(sessionId)
    sessions.value = sessions.value.filter(s => s.id !== sessionId)
    if (currentSessionId.value === sessionId) {
      sessionLoadVersion += 1
      stopGeneration()
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
  const requestVersion = ++sessionLoadVersion
  stopGeneration()
  const session = sessions.value.find(item => item.id === id)
  selectedProjectId.value = session?.project_id || null
  currentSessionId.value = id
  messages.splice(0)
  currentCitations.value = []
  try {
    const result = session?.project_id
      ? await chatApi.getMessages(session.project_id, id)
      : await chatApi.getGlobalMessages(id)
    if (requestVersion !== sessionLoadVersion || currentSessionId.value !== id) return
    messages.push(
      ...result.map((item: any) => ({
        ...item,
        // API 的持久化角色是 USER / ASSISTANT，展示组件使用小写联合类型。
        role: String(item.role || '').toLowerCase(),
        citations: item.citations || [],
        agent_trace: item.traces || [],
      })),
    )
  } catch (e: any) {
    ElMessage.error(e?.message || '加载消息失败')
  }
}

// 加载会话列表
async function loadSessions() {
  try {
    const [globalSessions, ...projectSessionLists] = await Promise.all([
      chatApi.getGlobalSessions(),
      ...projects.value.filter(project => project.id).map(project => chatApi.getSessions(project.id!)),
    ])
    const result = [...globalSessions, ...projectSessionLists.flat()]
    sessions.value = result.map(s => ({
      id: s.id,
      title: s.title || '新对话',
      updated_at: s.updated_at,
      project_id: s.project_id || null,
    })).sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    if (!currentSessionId.value && sessions.value.length > 0) {
      await switchSession(sessions.value[0].id)
    }
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
  if (!text.trim() || isStreaming.value || isPreparingTurn.value) return
  const currentTurnPreparationVersion = ++turnPreparationVersion
  isPreparingTurn.value = true
  try {
    // 当前会话在后端必须绑定项目：即便是法律问题，也要先确定权限边界和会话归属。
    // 之后由 Agent 按问题内容决定只检索法律库、项目资料，或两者同时检索。
    if (!projectsLoaded.value) await loadProjects()
    if (currentTurnPreparationVersion !== turnPreparationVersion) return
    // 确保有会话
    if (!currentSessionId.value) {
      try {
        await ensureCurrentSession(selectedProjectId.value || undefined)
      } catch {
        // continue with a server-created session
      }
    }
    if (currentTurnPreparationVersion !== turnPreparationVersion) return

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
  } finally {
    if (currentTurnPreparationVersion === turnPreparationVersion) {
      isPreparingTurn.value = false
    }
  }
}

async function confirmProjectAndSend() {
  const previousProjectId = selectedProjectId.value
  selectedProjectId.value = pendingProjectId.value
  showProjectPicker.value = false
  const project = projects.value.find(item => item.id === selectedProjectId.value)
  const question = pendingQuestion.value
  pendingQuestion.value = ''
  pendingProjectId.value = ''
  if (!project) return

  // A session must keep one project context. Switching projects starts a
  // clean session so previous-project history can never affect the answer.
  // 全局法律会话也不能复用为项目会话；两者的权限范围和 API 路径不同。
  if (previousProjectId !== project.id && currentSessionId.value) {
    const result = await chatApi.createSession(project.id, { title: '新对话' })
    sessions.value.unshift({
      id: result.id,
      title: result.title || '新对话',
      updated_at: result.updated_at,
      project_id: project.id,
    })
    currentSessionId.value = result.id
    messages.splice(0)
    currentCitations.value = []
  }

  ElMessage.success(`已切换至项目：${project.name}`)
  await loadSessions()
  if (question) await handleSend(question)
}

// 停止生成
function stopGeneration() {
  turnPreparationVersion += 1
  isPreparingTurn.value = false
  stopStream()
  const lastMsg = messages[messages.length - 1]
  if (lastMsg?.role === 'assistant' && lastMsg.streaming) {
    lastMsg.streaming = false
    lastMsg.status = 'ok'
  }
}

// 重新生成
async function handleRegenerate(index: number) {
  if (isStreaming.value || isPreparingTurn.value) return
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
  loadProjects().then(() => {
    loadSessions()
  })
})
</script>

<style scoped>
.chat-layout {
  display: flex;
  height: 100%;
  min-height: 0;
  overflow: hidden;
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
  min-height: 0;
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
.memory-btn{width:100%;margin-top:4px}.memory-create{display:flex;gap:10px;margin:14px 0}.memory-item{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--color-border)}.memory-item p{margin:8px 0 0;line-height:1.55;color:var(--color-text-secondary)}

.sidebar-history {
  flex: 1;
  min-height: 0;
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
  min-height: 0;
  background: var(--color-background);
}

.project-context-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 64px;
  padding: 10px var(--spacing-8);
  background: rgba(255, 255, 255, .84);
  border-bottom: 1px solid var(--color-border);
  box-shadow: 0 2px 8px rgba(15, 23, 42, .03);
}

.project-context-icon {
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  flex: 0 0 auto;
  color: #11875d;
  font-weight: 700;
  background: #dff7ed;
  border-radius: 50%;
}

.project-context-copy { display: grid; min-width: 0; gap: 1px; line-height: 1.4; }
.project-context-copy strong { overflow: hidden; color: var(--color-text-primary); font-size: var(--font-size-sm); text-overflow: ellipsis; white-space: nowrap; }
.project-context-copy > span:last-child { color: var(--color-text-muted); font-size: var(--font-size-xs); }
.project-context-label { color: #16865f; font-size: 11px; font-weight: 600; }
.project-switch-btn { margin-left: auto; flex: 0 0 auto; }

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
  .project-context-bar { padding-right: var(--spacing-4); padding-left: var(--spacing-4); }
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
