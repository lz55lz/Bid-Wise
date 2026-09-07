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
          :key="cite.evidence_id || cite.knowledge_chunk_id || cite.report_id || index"
          class="citation-item"
        >
          <span class="citation-index">[{{ index + 1 }}]</span>
          <div class="citation-content">
            <strong>{{ sourceLabel(cite.source) }}</strong>
            <div class="citation-quote" v-html="renderQuote(cite.quoted_text || cite.content || '原文节选不可用')" />
            <small v-if="cite.locator?.section_path">{{ cite.locator.section_path }}</small>
            <small v-else-if="cite.locator?.title">{{ cite.locator.title }}</small>
          </div>
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ArrowDown } from '@element-plus/icons-vue'
import DOMPurify from 'dompurify'
import { renderMarkdown } from '@/composables/useMarkdown'
import type { Citation } from '@/types'

const props = withDefaults(defineProps<{
  citations: Citation[]
  defaultExpanded?: boolean
}>(), {
  defaultExpanded: false,
})

const expanded = ref(props.defaultExpanded)

function sourceLabel(source?: Citation['source']) {
  if (source === 'LEGAL_KNOWLEDGE') return '法规依据'
  if (source === 'PROJECT_REPORT') return '项目报告'
  return '项目原文'
}

function renderQuote(value: unknown) {
  const text = String(value || '')
  // MinerU 表格偶尔产出 `\<table>`；只还原表格相关标签，避免把普通反斜杠误改为 HTML。
  const normalized = text.replace(/\\(?=<\/?(?:table|thead|tbody|tr|th|td)\b)/gi, '')
  if (!/<\/?table\b/i.test(normalized)) return renderMarkdown(normalized)
  return DOMPurify.sanitize(normalized, {
    ALLOWED_TAGS: ['table', 'thead', 'tbody', 'tr', 'th', 'td', 'p', 'br', 'strong', 'em'],
    ALLOWED_ATTR: ['rowspan', 'colspan'],
  })
}
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

.citation-content {
  flex: 1;
  min-width: 0;
}

.citation-content strong { color: var(--color-text-primary); font-size: 12px; }
.citation-quote { margin: 3px 0; color: var(--color-text-secondary); white-space: pre-wrap; }
.citation-quote :deep(p) { margin: 3px 0; }
.citation-quote :deep(table) { width: 100%; margin: 6px 0; border-collapse: collapse; white-space: normal; }
.citation-quote :deep(th), .citation-quote :deep(td) { padding: 6px 8px; border: 1px solid var(--color-border); text-align: left; vertical-align: top; }
.citation-quote :deep(th) { background: var(--color-background); font-weight: 600; }
.citation-content small { color: var(--color-text-muted); }

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
