<template>
  <div ref="listRef" class="chat-message-list">
    <div v-if="!messages.length" class="empty-wrapper">
      <slot name="empty">
        <ChatEmpty
          :title="emptyTitle"
          :subtitle="emptySubtitle"
          :prompts="emptyPrompts"
          @select="(prompt) => emit('selectPrompt', prompt)"
        />
      </slot>
    </div>
    <div v-else class="messages-wrapper">
      <ChatMessage
        v-for="(msg, index) in messages"
        :key="msg.id"
        :message="msg"
        @regenerate="emit('regenerate', index)"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import ChatMessage from './ChatMessage.vue'
import ChatEmpty from './ChatEmpty.vue'
import type { ChatMessage as ChatMessageType } from '@/types'

const props = withDefaults(defineProps<{
  messages: ChatMessageType[]
  emptyTitle?: string
  emptySubtitle?: string
  emptyPrompts?: string[]
}>(), {
  emptyTitle: '智能问答',
  emptySubtitle: '随时提问，获取投标与法律知识支持',
  emptyPrompts: () => [],
})

const emit = defineEmits<{
  selectPrompt: [prompt: string]
  regenerate: [index: number]
}>()

const listRef = ref<HTMLElement>()

const scrollToBottom = () => {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  })
}

watch(() => props.messages.length, scrollToBottom)
watch(() => props.messages.at(-1)?.content, scrollToBottom)
watch(() => props.messages.at(-1)?.streaming, scrollToBottom)
</script>

<style scoped>
.chat-message-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--spacing-6);
  scroll-behavior: smooth;
}

.chat-message-list::-webkit-scrollbar {
  width: 6px;
}

.chat-message-list::-webkit-scrollbar-track {
  background: transparent;
}

.chat-message-list::-webkit-scrollbar-thumb {
  background: var(--color-border);
  border-radius: 3px;
}

.chat-message-list::-webkit-scrollbar-thumb:hover {
  background: var(--color-border-hover);
}

.empty-wrapper {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.messages-wrapper {
  display: flex;
  flex-direction: column;
  gap: var(--spacing-6);
  max-width: 860px;
  margin: 0 auto;
}

@media (max-width: 768px) {
  .chat-message-list {
    padding: var(--spacing-4);
  }

  .messages-wrapper {
    gap: var(--spacing-4);
  }
}
</style>
