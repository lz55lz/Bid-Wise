<template>
  <div :class="['chat-message', message.role === 'user' ? 'user' : 'assistant']">
    <div class="message-avatar">
      <el-icon v-if="message.role === 'assistant'"><ChatLineRound /></el-icon>
      <el-icon v-else><User /></el-icon>
    </div>

    <div class="message-body">
      <div class="message-meta">
        <span class="message-role">{{ message.role === 'user' ? '我' : '助手' }}</span>
        <span class="message-time">{{ formattedTime }}</span>
      </div>

      <div class="message-bubble">
        <div v-if="isStreaming" class="typing-indicator">
          <span></span><span></span><span></span>
        </div>
        <MarkdownRenderer :content="message.content || ' '" />
      </div>

      <ChatCitations v-if="message.citations?.length" :citations="message.citations" />

      <div v-if="!isStreaming" class="message-actions">
        <button class="action-btn" title="复制" @click="copyContent">
          <el-icon><CopyDocument /></el-icon>
          <span>复制</span>
        </button>
        <button
          v-if="message.role === 'assistant'"
          class="action-btn"
          title="重新生成"
          @click="emit('regenerate')"
        >
          <el-icon><RefreshRight /></el-icon>
          <span>重新生成</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { ChatLineRound, User, CopyDocument, RefreshRight } from '@element-plus/icons-vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import ChatCitations from './ChatCitations.vue'
import { getRelativeTime } from '@/utils/format'
import type { ChatMessage } from '@/types'

const props = defineProps<{
  message: ChatMessage
}>()

const emit = defineEmits<{
  regenerate: []
}>()

const isStreaming = computed(() => props.message.streaming || props.message.status === 'streaming')
const formattedTime = computed(() => getRelativeTime(props.message.created_at))

async function copyContent() {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(props.message.content)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = props.message.content
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const success = document.execCommand('copy')
      document.body.removeChild(textarea)
      if (!success) throw new Error('copy failed')
    }
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败')
  }
}
</script>

<style scoped>
.chat-message {
  display: flex;
  gap: var(--spacing-4);
  animation: messageSlideIn 0.3s cubic-bezier(0.22, 0.61, 0.36, 1);
}

.chat-message.user {
  flex-direction: row-reverse;
}

@keyframes messageSlideIn {
  from {
    opacity: 0;
    transform: translateY(12px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.message-avatar {
  width: 40px;
  height: 40px;
  border-radius: var(--radius-lg);
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 18px;
  box-shadow: var(--shadow-md);
}

.user .message-avatar {
  background: linear-gradient(135deg, var(--color-primary), var(--color-secondary));
}

.assistant .message-avatar {
  background: linear-gradient(135deg, var(--color-accent), #0284c7);
}

.message-body {
  max-width: 72%;
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.user .message-body {
  align-items: flex-end;
}

.message-meta {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  font-size: var(--font-size-xs);
  padding: 0 var(--spacing-1);
}

.message-role {
  font-weight: 600;
  color: var(--color-text-secondary);
}

.user .message-role {
  color: var(--color-accent);
}

.message-time {
  color: var(--color-text-muted);
}

.message-bubble {
  padding: var(--spacing-4) var(--spacing-5);
  border-radius: var(--radius-xl);
  line-height: 1.75;
  word-break: break-word;
  box-shadow: var(--shadow-sm);
}

.user .message-bubble {
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  color: white;
  border-bottom-right-radius: var(--radius-md);
  box-shadow: var(--shadow-md), 0 4px 12px rgba(3, 105, 161, 0.25);
}

.assistant .message-bubble {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-bottom-left-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
}

.message-text {
  white-space: pre-wrap;
}

.message-actions {
  display: flex;
  gap: var(--spacing-1);
  opacity: 0;
  transition: opacity var(--transition-base);
  padding: 0 var(--spacing-1);
}

.chat-message:hover .message-actions {
  opacity: 1;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: var(--spacing-1);
  padding: var(--spacing-1) var(--spacing-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
}

.action-btn:hover {
  background: var(--color-background);
  color: var(--color-text-primary);
  border-color: var(--color-border-hover);
}

.typing-indicator {
  display: flex;
  gap: 5px;
  padding: var(--spacing-2) 0;
}

.typing-indicator span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  opacity: 0.6;
  animation: typingPulse 1.4s ease-in-out infinite;
}

.typing-indicator span:nth-child(2) { animation-delay: 0.15s; }
.typing-indicator span:nth-child(3) { animation-delay: 0.3s; }

@keyframes typingPulse {
  0%, 100% {
    opacity: 0.4;
    transform: scale(0.85);
  }
  50% {
    opacity: 1;
    transform: scale(1.1);
  }
}

@media (max-width: 768px) {
  .chat-message {
    gap: var(--spacing-3);
  }

  .message-avatar {
    width: 32px;
    height: 32px;
    font-size: 14px;
  }

  .message-body {
    max-width: 85%;
  }

  .message-bubble {
    padding: var(--spacing-3) var(--spacing-4);
  }

  .message-actions {
    opacity: 1;
  }
}
</style>
