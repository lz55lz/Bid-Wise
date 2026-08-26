<template>
  <div ref="containerRef" class="markdown-body" v-html="renderedHtml" />
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{
  content: string
}>()

const containerRef = ref<HTMLElement>()

const renderedHtml = computed(() => renderMarkdown(props.content))

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
