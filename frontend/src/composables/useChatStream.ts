import { ref } from 'vue'
import type { Citation } from '@/types'

export interface UseChatStreamOptions {
  // 默认项目上下文；startStream 的 target 可覆盖
  projectId?: string
  onDelta: (text: string) => void
  onDone: (answer: string, citations?: Citation[]) => void
  onError: (msg: string) => void
}

export interface ChatStreamTarget {
  projectId?: string
  sessionId?: string
}

export function useChatStream(options: UseChatStreamOptions) {
  const { projectId: defaultProjectId, onDelta, onDone, onError } = options
  const isStreaming = ref(false)
  let abortController: AbortController | null = null
  let sessionId: string | undefined

  const stopStream = () => {
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    isStreaming.value = false
  }

  // 项目与法律知识都经同一会话入口，后端决定检索源。
  const startStream = async (question: string, target?: ChatStreamTarget) => {
    stopStream()
    isStreaming.value = true

    const projectId = target?.projectId ?? defaultProjectId
    sessionId = target?.sessionId ?? sessionId
    const token = localStorage.getItem('access_token')
    abortController = new AbortController()

    const url = '/api/v1/chat/stream'

    let fullAnswer = ''

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ question, project_id: projectId, session_id: sessionId }),
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('响应流不可用')
      }

      const decoder = new TextDecoder()
      let buffer = ''
      let doneCalled = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const dataText = line.slice(6).trim()
          if (!dataText) continue

          try {
            const data = JSON.parse(dataText)
            if (data.type === 'delta' && typeof data.content === 'string') {
              fullAnswer += data.content
              onDelta(data.content)
            } else if (data.type === 'done') {
              doneCalled = true
              sessionId = data.session_id ?? sessionId
              onDone(data.answer ?? fullAnswer, data.citations ?? [])
            } else if (data.type === 'error') {
              doneCalled = true
              onError(data.message ?? '生成失败')
            }
          } catch {
            // 忽略解析失败的行
          }
        }
      }

      // 处理最后可能残留的完整行
      if (buffer.startsWith('data: ')) {
        const dataText = buffer.slice(6).trim()
        try {
          const data = JSON.parse(dataText)
          if (data.type === 'done') {
            doneCalled = true
            sessionId = data.session_id ?? sessionId
            onDone(data.answer ?? fullAnswer, data.citations ?? [])
          } else if (data.type === 'error') {
            doneCalled = true
            onError(data.message ?? '生成失败')
          }
        } catch {
          // 忽略
        }
      }

      if (!doneCalled) {
        onDone(fullAnswer, [])
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        onDone(fullAnswer, [])
      } else {
        onError(err.message ?? '网络错误')
      }
    } finally {
      abortController = null
      isStreaming.value = false
    }
  }

  return { startStream, stopStream, isStreaming }
}
