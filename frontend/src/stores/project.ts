import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { projectApi, documentApi } from '@/api'
import type { BidDocumentCard, Project } from '@/types'

export interface ProjectStage {
  key: 'document' | 'requirement' | 'risk' | 'match' | 'decision' | 'report'
  label: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  description: string
  lastAction?: string
  lastActionTime?: string
}

// parse_status 在文档当前版本上，文档本身没有该字段
export function docParseStatus(doc: BidDocumentCard): string {
  return doc.parse_status
}

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)
  const documents = ref<BidDocumentCard[]>([])
  const loading = ref(false)

  const projectStages = computed<ProjectStage[]>(() => {
    if (!currentProject.value) return []

    const hasReady = documents.value.some((d) => docParseStatus(d) === 'READY')
    const hasFailed = documents.value.some((d) => docParseStatus(d) === 'FAILED')
    const allReady = documents.value.length > 0 && hasReady && !documents.value.some((d) => docParseStatus(d) !== 'READY' && docParseStatus(d) !== 'FAILED')

    return [
      {
        key: 'document',
        label: '文档解析',
        status: documents.value.length === 0 ? 'pending' : allReady ? 'completed' : hasFailed && !allReady ? 'failed' : 'processing',
        description: '上传招标文件并完成解析',
      },
      {
        key: 'requirement',
        label: '需求抽取',
        status: 'pending',
        description: '从文档中抽取投标要求',
      },
      {
        key: 'risk',
        label: '风险检查',
        status: 'pending',
        description: '规则检查与风险识别',
      },
      {
        key: 'match',
        label: '材料匹配',
        status: 'pending',
        description: '企业材料与需求匹配',
      },
      {
        key: 'decision',
        label: '投标决策',
        status: 'pending',
        description: '生成建议并确认决策',
      },
      {
        key: 'report',
        label: '报告生成',
        status: 'pending',
        description: '生成投标分析报告',
      },
    ]
  })

  const fetchProjects = async (params?: { status?: string }) => {
    loading.value = true
    try {
      const response = await projectApi.list(params)
      projects.value = response
      return response
    } finally {
      loading.value = false
    }
  }

  const fetchProject = async (id: string) => {
    loading.value = true
    try {
      const response = await projectApi.get(id)
      currentProject.value = response
      return currentProject.value
    } finally {
      loading.value = false
    }
  }

  const fetchDocuments = async (projectId: string) => {
    documents.value = await documentApi.list(projectId)
    return documents.value
  }

  const createProject = async (data: any) => {
    const response = await projectApi.create(data)
    projects.value.unshift(response)
    return response
  }

  const updateProject = async (id: string, data: Partial<Project>) => {
    const response = await projectApi.update(id, data)
    if (currentProject.value?.id === id) {
      currentProject.value = response
    }
    return response
  }

  const archiveProject = async (id: string) => {
    await projectApi.archive(id)
    await fetchProjects()
  }

  const deleteProject = async (id: string) => {
    await projectApi.delete(id)
    await fetchProjects()
  }

  const clearCurrentProject = () => {
    currentProject.value = null
    documents.value = []
  }

  return {
    projects,
    currentProject,
    documents,
    loading,
    projectStages,
    fetchProjects,
    fetchProject,
    fetchDocuments,
    createProject,
    updateProject,
    archiveProject,
    deleteProject,
    clearCurrentProject,
  }
})
