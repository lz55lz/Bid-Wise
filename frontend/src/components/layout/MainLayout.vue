<template>
  <el-container class="main-layout">
    <!-- 侧边栏 -->
    <el-aside :width="isCollapsed ? '64px' : '240px'" class="sidebar">
      <div class="sidebar-header">
        <div class="logo">
          <svg viewBox="0 0 32 32" fill="none" class="logo-icon">
            <rect width="32" height="32" rx="8" fill="var(--color-primary)"/>
            <path d="M8 10h16M8 16h10M8 22h13" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round"/>
            <circle cx="24" cy="22" r="4" fill="var(--color-accent)"/>
            <path d="M22.5 22l1 1 2-2" stroke="#FFFFFF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
          <span v-if="!isCollapsed" class="logo-text">BidWise</span>
        </div>
        <el-button
          :icon="isCollapsed ? 'Expand' : 'Fold'"
          text
          class="collapse-btn"
          @click="isCollapsed = !isCollapsed"
        />
      </div>

      <el-menu
        :default-active="activeMenu"
        :collapse="isCollapsed"
        :collapse-transition="false"
        class="sidebar-menu"
      >
        <el-menu-item index="/" @click="router.push('/')">
          <el-icon><HomeFilled /></el-icon>
          <template #title>工作台</template>
        </el-menu-item>

        <el-menu-item index="/projects" @click="router.push('/projects')">
          <el-icon><FolderOpened /></el-icon>
          <template #title>项目</template>
        </el-menu-item>

        <el-menu-item index="/materials" @click="router.push('/materials')">
          <el-icon><Collection /></el-icon>
          <template #title>企业材料</template>
        </el-menu-item>

        <el-menu-item index="/knowledge" @click="router.push('/knowledge')">
          <el-icon><Reading /></el-icon>
          <template #title>知识库</template>
        </el-menu-item>

        <el-menu-item index="/risks" @click="router.push('/risks')">
          <el-icon><Warning /></el-icon>
          <template #title>风险中心</template>
        </el-menu-item>

        <el-menu-item index="/reports" @click="router.push('/reports')">
          <el-icon><Document /></el-icon>
          <template #title>报告中心</template>
        </el-menu-item>

        <el-menu-item index="/chat" @click="router.push('/chat')">
          <el-icon><ChatDotRound /></el-icon>
          <template #title>智能问答</template>
        </el-menu-item>

        <el-divider />

        <el-menu-item index="/settings" @click="router.push('/settings')">
          <el-icon><Setting /></el-icon>
          <template #title>系统设置</template>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部栏 -->
      <el-header class="header">
        <div class="header-left">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item :to="{ path: '/' }">首页</el-breadcrumb-item>
            <el-breadcrumb-item v-if="breadcrumbs.length" v-for="item in breadcrumbs" :key="item.path">
              {{ item.title }}
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div class="header-right">
          <el-dropdown @command="handleCommand">
            <span class="user-info">
              <el-avatar :size="32" class="user-avatar">
                {{ user?.username?.charAt(0)?.toUpperCase() || 'U' }}
              </el-avatar>
              <span class="user-name">{{ user?.username || '用户' }}</span>
              <el-icon class="user-arrow"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="profile">
                  <el-icon><User /></el-icon>
                  个人中心
                </el-dropdown-item>
                <el-dropdown-item command="settings">
                  <el-icon><Setting /></el-icon>
                  系统设置
                </el-dropdown-item>
                <el-dropdown-item divided command="logout">
                  <el-icon><SwitchButton /></el-icon>
                  退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主内容区 -->
      <el-main class="main-content">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { ElMessageBox } from 'element-plus'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()

const isCollapsed = ref(false)
const user = computed(() => authStore.user)

const activeMenu = computed(() => {
  if (route.path === '/') return '/'
  if (route.path.startsWith('/projects')) return '/projects'
  if (route.path.startsWith('/materials')) return '/materials'
  if (route.path.startsWith('/risks')) return '/risks'
  if (route.path.startsWith('/reports')) return '/reports'
  if (route.path === '/chat') return '/chat'
  if (route.path.startsWith('/knowledge/chat')) return '/knowledge/chat'
  if (route.path.startsWith('/knowledge')) return '/knowledge'
  if (route.path.startsWith('/settings')) return '/settings'
  return route.path
})

const breadcrumbs = computed(() => {
  const crumbs: { path: string; title: string }[] = []
  const title = route.meta.title as string
  if (title && title !== '工作台') {
    crumbs.push({ path: route.path, title })
  }
  return crumbs
})

const handleCommand = async (command: string) => {
  switch (command) {
    case 'logout':
      try {
        await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
          confirmButtonText: '确定',
          cancelButtonText: '取消',
          type: 'info',
        })
        await authStore.logout()
        router.push('/login')
      } catch {
        // cancel
      }
      break
    case 'profile':
      router.push('/settings')
      break
    case 'settings':
      router.push('/settings')
      break
  }
}
</script>

<style scoped>
.main-layout {
  min-height: 100vh;
}

.sidebar {
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  transition: width var(--transition-base);
}

.sidebar-header {
  height: 64px;
  padding: 0 var(--spacing-4);
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--color-border);
}

.logo {
  display: flex;
  align-items: center;
  gap: var(--spacing-3);
}

.logo-icon {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
}

.logo-text {
  font-size: var(--font-size-xl);
  font-weight: 700;
  color: var(--color-text-primary);
  white-space: nowrap;
}

.collapse-btn {
  padding: var(--spacing-2);
}

.sidebar-menu {
  flex: 1;
  border-right: none;
  padding: var(--spacing-2) 0;
}

.header {
  height: 64px;
  padding: 0 var(--spacing-6);
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: var(--spacing-4);
}

.user-info {
  display: flex;
  align-items: center;
  gap: var(--spacing-2);
  cursor: pointer;
  padding: var(--spacing-2);
  border-radius: var(--radius-md);
  transition: background-color var(--transition-fast);
}

.user-info:hover {
  background-color: var(--color-background);
}

.user-avatar {
  background: var(--color-accent);
  color: white;
  font-weight: 600;
}

.user-name {
  font-size: var(--font-size-sm);
  font-weight: 500;
  color: var(--color-text-primary);
}

.user-arrow {
  color: var(--color-text-muted);
  font-size: 12px;
}

.main-content {
  background: var(--color-background);
  padding: var(--spacing-6);
  overflow-y: auto;
}
</style>
