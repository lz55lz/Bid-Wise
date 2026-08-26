<template>
  <div class="chat-citations">
    <div class="citations-header" @click="expanded = !expanded">
      <span>参考来源 ({{ citations.length }})</span>
      <el-icon class="expand-icon" :class="{ expanded }"><ArrowDown /></el-icon>
    </div>
    <transition name="fade">
      <div v-show="expanded" class="citations-list">
        <div
          v-for="(cite, index) in citations"
          :key="cite.evidence_id"
          class="citation-item"
        >
          <span class="citation-index">[{{ index + 1 }}]</span>
          <span class="citation-id">{{ cite.evidence_id.slice(0, 8) }}</span>
          <span v-if="cite.content" class="citation-content">{{ cite.content }}</span>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import type { Citation } from '@/types'

const props = withDefaults(defineProps<{
  citations: Citation[]
  defaultExpanded?: boolean
}>(), {
  defaultExpanded: false,
})

const expanded = ref(props.defaultExpanded)
</script>

<style scoped>
.chat-citations {
  margin-top: var(--spacing-3);
  background: rgba(3, 105, 161, 0.04);
  border: 1px solid rgba(3, 105, 161, 0.12);
  border-radius: var(--radius-lg);
  overflow: hidden;
}

.citations-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--spacing-2) var(--spacing-3);
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--color-accent);
  cursor: pointer;
  user-select: none;
  transition: background var(--transition-fast);
}

.citations-header:hover {
  background: rgba(3, 105, 161, 0.08);
}

.expand-icon {
  transition: transform var(--transition-base);
}

.expand-icon.expanded {
  transform: rotate(180deg);
}

.citations-list {
  padding: var(--spacing-2) var(--spacing-3) var(--spacing-3);
  display: flex;
  flex-direction: column;
  gap: var(--spacing-2);
}

.citation-item {
  display: flex;
  align-items: flex-start;
  gap: var(--spacing-2);
  padding: var(--spacing-2);
  background: var(--color-surface);
  border-radius: var(--radius-md);
  font-size: var(--font-size-xs);
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.citation-index {
  color: var(--color-accent);
  flex-shrink: 0;
  font-weight: 600;
  min-width: 18px;
}

.citation-id {
  color: var(--color-text-muted);
  flex-shrink: 0;
  font-family: 'SF Mono', Monaco, monospace;
  font-size: 10px;
  padding: 1px 4px;
  background: var(--color-background);
  border-radius: var(--radius-sm);
}

.citation-content {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity var(--transition-base), transform var(--transition-base);
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}
</style>
