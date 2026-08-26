<template>
  <div class="chat-empty">
    <div class="empty-content">
      <div class="empty-icon">
        <el-icon><ChatLineRound /></el-icon>
      </div>
      <h2 class="empty-title">{{ title }}</h2>
      <p v-if="subtitle" class="empty-subtitle">{{ subtitle }}</p>
      <div v-if="prompts.length" class="quick-prompts">
        <button
          v-for="prompt in prompts"
          :key="prompt"
          class="quick-prompt-btn"
          @click="emit('select', prompt)"
        >
          {{ prompt }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ChatLineRound } from '@element-plus/icons-vue'

withDefaults(defineProps<{
  title?: string
  subtitle?: string
  prompts?: string[]
}>(), {
  title: '智能问答',
  subtitle: '随时提问，获取投标与法律知识支持',
  prompts: () => [],
})

const emit = defineEmits<{
  select: [prompt: string]
}>()
</script>

<style scoped>
.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: var(--spacing-12);
}

.empty-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--spacing-5);
  max-width: 560px;
}

.empty-icon {
  width: 80px;
  height: 80px;
  border-radius: var(--radius-2xl);
  background: linear-gradient(135deg, var(--color-accent), var(--color-accent-hover));
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 36px;
  box-shadow: var(--shadow-xl), 0 8px 24px rgba(3, 105, 161, 0.3);
  animation: floatIn 0.6s cubic-bezier(0.22, 0.61, 0.36, 1);
}

@keyframes floatIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.9);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.empty-title {
  font-size: var(--font-size-3xl);
  font-weight: 700;
  color: var(--color-text-primary);
  margin: 0;
  letter-spacing: -0.02em;
}

.empty-subtitle {
  font-size: var(--font-size-base);
  color: var(--color-text-secondary);
  margin: 0;
  line-height: 1.6;
  max-width: 400px;
}

.quick-prompts {
  display: flex;
  flex-wrap: wrap;
  gap: var(--spacing-3);
  justify-content: center;
  margin-top: var(--spacing-4);
}

.quick-prompt-btn {
  padding: var(--spacing-3) var(--spacing-5);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  font-size: var(--font-size-sm);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-base);
  box-shadow: var(--shadow-sm);
}

.quick-prompt-btn:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
  background: rgba(3, 105, 161, 0.05);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.quick-prompt-btn:active {
  transform: translateY(0);
}

@media (max-width: 768px) {
  .chat-empty {
    padding: var(--spacing-8);
  }

  .empty-icon {
    width: 64px;
    height: 64px;
    font-size: 28px;
  }

  .empty-title {
    font-size: var(--font-size-2xl);
  }

  .quick-prompts {
    gap: var(--spacing-2);
  }

  .quick-prompt-btn {
    padding: var(--spacing-2) var(--spacing-4);
    font-size: var(--font-size-xs);
  }
}
</style>
