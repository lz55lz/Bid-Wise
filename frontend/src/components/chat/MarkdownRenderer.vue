<template>
  <div ref="containerRef" class="markdown-body" v-html="renderedHtml" />
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/composables/useMarkdown'
import type { Citation } from '@/types'

const props = defineProps<{
  content: string
  citations?: Citation[]
}>()

const containerRef = ref<HTMLElement>()

const renderedHtml = computed(() => renderMarkdown(replaceCitationIds(props.content, props.citations || [])))

/**
 * UUID 是审计关联键，不是阅读文案。正文中将其替换为稳定的序号，原文和章节信息
 * 由下方的「参考来源」卡片呈现；复制/导出的 Markdown 仍保留后端原始证据 ID。
 */
function replaceCitationIds(content: string, citations: Citation[]): string {
  const projectIndex = new Map<string, number>()
  const legalIndex = new Map<string, number>()
  const reportIndex = new Map<string, number>()
  let projectCount = 0
  let legalCount = 0
  let reportCount = 0
  for (const citation of citations) {
    if (citation.evidence_id && !projectIndex.has(citation.evidence_id)) {
      projectIndex.set(citation.evidence_id, ++projectCount)
    }
    if (citation.knowledge_chunk_id && !legalIndex.has(citation.knowledge_chunk_id)) {
      legalIndex.set(citation.knowledge_chunk_id, ++legalCount)
    }
    if (citation.report_id && !reportIndex.has(citation.report_id)) {
      reportIndex.set(citation.report_id, ++reportCount)
    }
  }
  return content.replace(/【(Evidence|Legal|Report):\s*([0-9a-fA-F-]{36})】/g, (_raw, kind, id) => {
    const index = kind === 'Evidence'
      ? projectIndex.get(id)
      : kind === 'Legal'
        ? legalIndex.get(id)
        : reportIndex.get(id)
    const label = kind === 'Evidence' ? '项目证据' : kind === 'Legal' ? '法规依据' : '项目报告'
    return index ? `【${label} ${index}】` : `【${label}】`
  })
}

async function copyCode(code: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(code)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = code
      textarea.style.position = 'fixed'
      textarea.style.opacity = '0'
      document.body.appendChild(textarea)
      textarea.select()
      const success = document.execCommand('copy')
      document.body.removeChild(textarea)
      if (!success) throw new Error('execCommand copy failed')
    }
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.error('复制失败')
  }
}

function addCopyButtons() {
  nextTick(() => {
    const container = containerRef.value
    if (!container) return

    const preBlocks = container.querySelectorAll('pre')
    preBlocks.forEach((pre) => {
      if (pre.parentElement?.classList.contains('code-block-wrapper')) return

      const code = pre.querySelector('code')
      const text = code?.textContent ?? pre.textContent ?? ''

      const wrapper = document.createElement('div')
      wrapper.className = 'code-block-wrapper'
      pre.parentNode?.insertBefore(wrapper, pre)
      wrapper.appendChild(pre)

      const btn = document.createElement('button')
      btn.className = 'code-copy-btn'
      btn.type = 'button'
      btn.title = '复制代码'
      btn.textContent = '复制'
      btn.addEventListener('click', () => {
        copyCode(text)
        btn.textContent = '已复制'
        setTimeout(() => {
          btn.textContent = '复制'
        }, 2000)
      })
      wrapper.appendChild(btn)
    })
  })
}

watch(() => props.content, addCopyButtons, { immediate: true })
</script>

<style scoped>
.markdown-body {
  line-height: 1.75;
  color: inherit;
}

.markdown-body :deep(p) {
  margin-bottom: var(--spacing-3);
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3),
.markdown-body :deep(h4) {
  margin-top: var(--spacing-4);
  margin-bottom: var(--spacing-2);
  font-weight: 600;
  color: inherit;
}

.markdown-body :deep(h1) { font-size: 1.25em; }
.markdown-body :deep(h2) { font-size: 1.1em; }
.markdown-body :deep(h3), .markdown-body :deep(h4) { font-size: 1em; }

.markdown-body :deep(code) {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
  font-size: 0.9em;
  padding: 0.15em 0.4em;
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.06);
  color: inherit;
}

.markdown-body :deep(pre) {
  margin: var(--spacing-3) 0;
  padding: var(--spacing-3);
  border-radius: var(--radius-lg);
  background: #1e293b;
  color: #f8fafc;
  overflow-x: auto;
  line-height: 1.5;
}

.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
  font-size: var(--font-size-xs);
}

.markdown-body :deep(blockquote) {
  margin: var(--spacing-3) 0;
  padding: var(--spacing-2) var(--spacing-4);
  border-left: 3px solid var(--color-accent);
  background: rgba(3, 105, 161, 0.05);
  border-radius: 0 var(--radius-md) var(--radius-md) 0;
  color: var(--color-text-secondary);
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin-bottom: var(--spacing-3);
  padding-left: var(--spacing-5);
}

.markdown-body :deep(li) {
  margin-bottom: var(--spacing-1);
}

.markdown-body :deep(a) {
  color: var(--color-accent);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.markdown-body :deep(a:hover) {
  color: var(--color-accent-hover);
}

.markdown-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: var(--spacing-3) 0;
  font-size: var(--font-size-xs);
}

.markdown-body :deep(th),
.markdown-body :deep(td) {
  padding: var(--spacing-2) var(--spacing-3);
  border: 1px solid var(--color-border);
  text-align: left;
}

.markdown-body :deep(th) {
  background: var(--color-background);
  font-weight: 600;
}

.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: var(--spacing-4) 0;
}
</style>
