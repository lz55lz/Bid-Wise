import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import NProgress from 'nprogress'
import 'nprogress/nprogress.css'
import { useAuthStore } from '@/stores'

NProgress.configure({ showSpinner: false })

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/auth/Login.vue'),
    meta: { requiresAuth: false, title: '登录' },
  },
  {
    path: '/',
    component: () => import('@/components/layout/MainLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Dashboard',
        component: () => import('@/views/dashboard/Dashboard.vue'),
        meta: { title: '工作台' },
      },
      {
        path: 'projects',
        name: 'ProjectList',
        component: () => import('@/views/project/ProjectList.vue'),
        meta: { title: '项目管理' },
      },
      {
        path: 'projects/:id',
        name: 'ProjectDetail',
        component: () => import('@/views/project/ProjectDetail.vue'),
        meta: { title: '项目详情' },
      },
      {
        path: 'projects/:projectId/documents/:documentId',
        name: 'DocumentReader',
        component: () => import('@/views/document/DocumentReader.vue'),
        meta: { title: '文档阅读' },
      },
      {
        path: 'risks',
        name: 'RiskCenter',
        component: () => import('@/views/risk/RiskCenter.vue'),
        meta: { title: '风险中心' },
      },
      {
        path: 'materials',
        name: 'MaterialLibrary',
        component: () => import('@/views/material/MaterialLibrary.vue'),
        meta: { title: '企业材料库' },
      },
      {
        path: 'knowledge',
        name: 'KnowledgeBase',
        component: () => import('@/views/knowledge/KnowledgeBase.vue'),
        meta: { title: '知识库管理' },
      },
      {
        path: 'reports',
        name: 'ReportList',
        component: () => import('@/views/decision/ReportList.vue'),
        meta: { title: '报告中心' },
      },
      {
        path: 'chat',
        name: 'Chat',
        component: () => import('@/views/chat/index.vue'),
        meta: { title: '智能问答' },
      },
      {
        path: 'evaluation',
        name: 'EvaluationCenter',
        component: () => import('@/views/evaluation/EvaluationCenter.vue'),
        meta: { title: '评测中心' },
      },
      {
        path: 'settings',
        name: 'Settings',
        component: () => import('@/views/settings/Settings.vue'),
        meta: { title: '系统设置' },
      },
    ],
  },
  {
    path: '/:pathMatch(.*)*',
    name: 'NotFound',
    component: () => import('@/views/NotFound.vue'),
    meta: { title: '页面不存在' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    }
    return { top: 0 }
  },
})

// 路由守卫
router.beforeEach(async (to, _from, next) => {
  NProgress.start()

  const authStore = useAuthStore()
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth !== false)

  // 设置页面标题
  document.title = `${to.meta.title || 'BidWise'} - BidWise`

  if (requiresAuth && !authStore.isAuthenticated()) {
    // 检查是否有 token
    if (authStore.token) {
      try {
        await authStore.fetchCurrentUser()
        next()
      } catch {
        next('/login')
      }
    } else {
      next('/login')
    }
  } else if (to.path === '/login' && authStore.isAuthenticated()) {
    next('/')
  } else {
    next()
  }
})

router.afterEach(() => {
  NProgress.done()
})

export default router
