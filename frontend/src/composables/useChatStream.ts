import { ref } from 'vue'
import type { AgentTraceItem, Citation } from '@/types'

export interface UseChatStreamOptions {
  // 默认项目上下文；startStream 的 target 可覆盖
  projectId?: string
  onDelta: (text: string) => void
  onDone: (
    answer: string,
    citations?: Citation[],
    agentTrace?: AgentTraceItem[],
    projectId?: string,
    sessionId?: string,
  ) => void
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
  // A stream may finish after the user has stopped it or switched sessions.
  // Keep those stale callbacks from updating the currently displayed chat.
  let streamVersion = 0

  const stopStream = () => {
    streamVersion += 1
    if (abortController) {
      abortController.abort()
      abortController = null
    }
    isStreaming.value = false
  }

  // 项目与法律知识都经同一会话入口，后端决定检索源。
  const startStream = async (question: string, target?: ChatStreamTarget) => {
    stopStream()
    const currentStreamVersion = ++streamVersion
    isStreaming.value = true

    const projectId = target?.projectId ?? defaultProjectId
    sessionId = target?.sessionId ?? sessionId
    const token = localStorage.getItem('access_token')
    abortController = new AbortController()

    if (!sessionId) {
      isStreaming.value = false
      onError('请先创建会话')
      return
    }
    const url = projectId
      ? `/api/v1/projects/${projectId}/conversations/${sessionId}/messages/stream`
      : `/api/v1/conversations/${sessionId}/messages/stream`

    let fullAnswer = ''
    const agentTrace: AgentTraceItem[] = []

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ content: question }),
        signal: abortController.signal,
      })

      if (!response.ok) {
        const payload = await response.json().catch(() => null)
        throw new Error(payload?.message ?? `HTTP ${response.status}`)
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
          if (currentStreamVersion !== streamVersion) return
          if (!line.startsWith('data: ')) continue
          const dataText = line.slice(6).trim()
          if (!dataText) continue

          try {
            const data = JSON.parse(dataText)
            if (data.type === 'token' && typeof data.content === 'string') {
              fullAnswer += data.content
              onDelta(data.content)
            } else if (data.type === 'done') {
              doneCalled = true
              const message = data.message || {}
              onDone(
                message.content ?? fullAnswer,
                message.citations ?? [],
                message.traces ?? agentTrace,
                projectId,
                sessionId,
              )
            } else if (data.type === 'agent_trace') {
              agentTrace.push(data as AgentTraceItem)
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
      if (currentStreamVersion === streamVersion && buffer.startsWith('data: ')) {
        const dataText = buffer.slice(6).trim()
        try {
          const data = JSON.parse(dataText)
          if (data.type === 'done') {
            doneCalled = true
            const message = data.message || {}
            onDone(
              message.content ?? fullAnswer,
              message.citations ?? [],
              message.traces ?? agentTrace,
              projectId,
              sessionId,
            )
          } else if (data.type === 'error') {
            doneCalled = true
            onError(data.message ?? '生成失败')
          }
        } catch {
          // 忽略
        }
      }

      if (currentStreamVersion === streamVersion && !doneCalled && fullAnswer) {
        onDone(fullAnswer, [])
      }
    } catch (err: any) {
      if (currentStreamVersion !== streamVersion) return
      if (err.name === 'AbortError') {
        onDone(fullAnswer, [])
      } else {
        onError(err.message ?? '网络错误')
      }
    } finally {
      if (currentStreamVersion === streamVersion) {
        abortController = null
        isStreaming.value = false
      }
    }
  }

  return { startStream, stopStream, isStreaming }
}
