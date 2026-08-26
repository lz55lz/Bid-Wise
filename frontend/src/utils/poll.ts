import { taskApi } from '@/api'
import type { Task } from '@/types'

// 轮询后端异步任务直到终态（SUCCEEDED/FAILED）
export async function pollTask(
  taskId: string,
  { intervalMs = 2000, timeoutMs = 300000 }: { intervalMs?: number; timeoutMs?: number } = {},
): Promise<Task> {
  const startedAt = Date.now()
  for (;;) {
    const task = await taskApi.get(taskId)
    if (task.status === 'SUCCEEDED' || task.status === 'FAILED') {
      return task
    }
    if (Date.now() - startedAt > timeoutMs) {
      throw new Error('任务执行超时，请稍后刷新查看结果')
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs))
  }
}
