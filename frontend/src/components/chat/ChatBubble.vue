<template>
  <div :class="['chat-message', message.role === 'user' ? 'user' : 'assistant']">
    <div class="message-content">
      <div class="message-bubble">
        <div v-if="message.role === 'assistant'" class="avatar">
          <el-icon><ChatLineRound /></el-icon>
        </div>
        <MarkdownRenderer v-if="message.role === 'assistant'" :content="message.content" class="text" />
        <div v-else class="text">{{ message.content }}</div>
        <div v-if="message.role === 'user'" class="avatar">
          <el-icon><User /></el-icon>
        </div>
      </div>
      <div v-if="message.citations && message.citations.length" class="citations">
        <div class="citations-title">参考证据：</div>
        <div v-for="cite in message.citations" :key="cite.evidence_id" class="citation-item">
          <el-tag size="small" type="info">{{ cite.evidence_id.slice(0, 8) }}...</el-tag>
          <span v-if="cite.content" class="citation-content">{{ cite.content }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChatLineRound, User } from '@element-plus/icons-vue'
import MarkdownRenderer from './MarkdownRenderer.vue'
import type { ChatMessage } from '@/types'

defineProps<{
  message: ChatMessage
}>()
</script>

<style scoped>
.chat-message {
  display: flex;
  margin-bottom: var(--spacing-4);
}

.chat-message.user {
  justify-content: flex-end;
}

.chat-message.assistant {
  justify-content: flex-start;
}

.message-content {
  max-width: 75%;
}

.message-bubble {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-2);
  padding: var(--spacing-3) var(--spacing-4);
  border-radius: var(--radius-lg);
}

.user .message-bubble {
  background: var(--color-accent);
  color: white;
}

.assistant .message-bubble {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.avatar {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
}

.user .avatar {
  color: white;
}

.text {
  flex: 1;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.5;
}

.citations {
  margin-top: var(--spacing-2);
  padding: var(--spacing-2);
  background: var(--color-background);
  border-radius: var(--radius);
  font-size: var(--font-size-xs);
}

.citations-title {
  color: var(--color-text-muted);
  margin-bottom: var(--spacing-1);
}

.citation-item {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  margin-bottom: var(--spacing-1);
}

.citation-content {
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
