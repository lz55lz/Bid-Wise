<template>
  <router-view />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores'

const authStore = useAuthStore()

onMounted(async () => {
  // 尝试恢复登录状态
  if (authStore.token) {
    await authStore.fetchCurrentUser()
  }
})
</script>

<style>
#app {
  min-height: 100vh;
}
</style>
