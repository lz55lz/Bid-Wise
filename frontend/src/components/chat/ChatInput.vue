<template>
  <div class="chat-input">
    <div class="input-toolbar">
      <div class="toolbar-left">
        <slot name="left-actions" />
        <button class="tool-btn" title="附件" @click="emit('attach')">
          <el-icon><Paperclip /></el-icon>
        </button>
      </div>
      <div class="toolbar-right">
        <span class="hint">Enter 发送，Shift+Enter 换行</span>
      </div>
    </div>

    <div class="input-box">
      <textarea
        ref="textareaRef"
        v-model="inputText"
        class="input-textarea"
        :rows="currentRows"
        :placeholder="placeholder"
        @keydown="handleKeydown"
        @input="adjustRows"
      />
    </div>

    <div class="send-bar">
      <button
        v-if="isStreaming"
        class="stop-btn"
        @click="emit('stop')"
      >
        <el-icon><CircleClose /></el-icon>
        <span>停止生成</span>
      </button>
      <button
        v-else
        class="send-btn"
        :disabled="!inputText.trim()"
        @click="handleSend"
      >
        <el-icon><Promotion /></el-icon>
        <span>发送</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { Promotion, Paperclip, CircleClose } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  modelValue: string
  isStreaming?: boolean
  placeholder?: string
  minRows?: number
  maxRows?: number
}>(), {
  isStreaming: false,
  placeholder: '输入问题，按 Enter 发送...',
  minRows: 2,
  maxRows: 8,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: [question: string]
  stop: []
  attach: []
}>()

const textareaRef = ref<HTMLTextAreaElement>()
const currentRows = ref(props.minRows)

const inputText = computed({
  get: () => props.modelValue,
  set: (value) => emit('update:modelValue', value),
})

function adjustRows() {
  nextTick(() => {
    const textarea = textareaRef.value
    if (!textarea) return

    textarea.style.height = 'auto'
    const lineHeight = parseInt(getComputedStyle(textarea).lineHeight) || 24
    const minHeight = lineHeight * props.minRows
    const maxHeight = lineHeight * props.maxRows
    const scrollHeight = textarea.scrollHeight

    const newHeight = Math.max(minHeight, Math.min(scrollHeight, maxHeight))
    textarea.style.height = `${newHeight}px`

    const newRows = Math.round(newHeight / lineHeight)
    currentRows.value = Math.max(props.minRows, Math.min(newRows, props.maxRows))
  })
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || props.isStreaming) return
  emit('send', text)
  emit('update:modelValue', '')
  currentRows.value = props.minRows
  nextTick(() => {
    if (textareaRef.value) {
      textareaRef.value.style.height = 'auto'
    }
  })
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

watch(() => props.modelValue, adjustRows, { immediate: true })
</script>

<style scoped>
.chat-input {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-3);
  padding: var(--spacing-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-2xl);
  box-shadow: var(--shadow-lg);
  transition: box-shadow var(--transition-base);
}

.chat-input:focus-within {
  box-shadow: var(--shadow-xl), 0 0 0 3px rgba(3, 105, 161, 0.1);
}

.input-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.toolbar-left {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
}

.tool-btn {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  padding: var(--spacing-1) var(--spacing-3);
  height: 32px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-background);
  color: var(--color-text-secondary);
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.tool-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: rgba(3, 105, 161, 0.05);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.hint {
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.input-box {
  position: relative;
}

.input-textarea {
  width: 100%;
  min-height: calc(2 * 1.5em);
  padding: var(--spacing-2) 0;
  border: none;
  outline: none;
  resize: none;
  font-family: inherit;
  font-size: var(--font-size-base);
  line-height: 1.6;
  color: var(--color-text-primary);
  background: transparent;
}

.input-textarea::placeholder {
  color: var(--color-text-muted);
}

.send-bar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: var(--spacing-3);
}

.send-btn,
.stop-btn {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  padding: var(--spacing-2) var(--spacing-5);
  height: 40px;
  border: none;
  border-radius: var(--radius-lg);
  font-size: var(--font-size-sm);
  font-weight: 600;
  cursor: pointer;
  transition: all var(--transition-fast);
}

.send-btn {
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  color: white;
  box-shadow: 0 2px 8px rgba(3, 105, 161, 0.3);
}

.send-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(3, 105, 161, 0.4);
}

.send-btn:active:not(:disabled) {
  transform: translateY(0);
}

.send-btn:disabled {
  background: var(--color-border);
  color: var(--color-text-muted);
  cursor: not-allowed;
  box-shadow: none;
}

.stop-btn {
  background: linear-gradient(135deg, var(--color-destructive), #b91c1c);
  color: white;
  box-shadow: 0 2px 8px rgba(220, 38, 38, 0.3);
}

.stop-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(220, 38, 38, 0.4);
}

@media (max-width: 768px) {
  .chat-input {
    padding: var(--spacing-3);
    border-radius: var(--radius-xl);
  }

  .toolbar-right {
    display: none;
  }

  .send-btn,
  .stop-btn {
    padding: var(--spacing-2) var(--spacing-4);
    height: 36px;
  }
}
</style>
