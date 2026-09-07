import { defineStore } from 'pinia'
import { ref } from 'vue'
import { authApi } from '@/api'
import type { User } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(localStorage.getItem('access_token'))

  const setToken = (newToken: string) => {
    token.value = newToken
    localStorage.setItem('access_token', newToken)
  }

  const setUser = (newUser: User) => {
    user.value = newUser
  }

  const login = async (username: string, password: string) => {
    const response = await authApi.login({ username, password })
    setToken(response.access_token)
    setUser(response.user)
  }

  const logout = async () => {
    try {
      await authApi.logout()
    } catch {
      // ignore
    } finally {
      token.value = null
      user.value = null
      localStorage.removeItem('access_token')
    }
  }

  const fetchCurrentUser = async () => {
    if (!token.value) return null
    try {
      const response = await authApi.getCurrentUser()
      setUser(response)
      return response
    } catch {
      // 当前用户回查失败不调用登出接口：否则在路由或接口配置出错时，
      // 会把一个仍有效的令牌意外加入服务端撤销列表。
      token.value = null
      user.value = null
      localStorage.removeItem('access_token')
      return null
    }
  }

  const isAuthenticated = () => !!token.value

  return {
    user,
    token,
    login,
    logout,
    fetchCurrentUser,
    isAuthenticated,
  }
})
