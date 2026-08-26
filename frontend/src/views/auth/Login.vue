<template>
  <main class="login-page">
    <section class="login-card" aria-label="系统登录">
      <header class="login-header">
        <div class="brand-mark" aria-hidden="true"><el-icon><DocumentChecked /></el-icon></div>
        <h1>BidWise</h1>
      </header>
      <el-form ref="formRef" :model="formData" :rules="rules" class="login-form" @submit.prevent="handleLogin">
        <el-form-item prop="username">
          <label class="form-label">用户名</label>
          <el-input v-model="formData.username" placeholder="请输入用户名" size="large" :prefix-icon="User" autocomplete="username" />
        </el-form-item>
        <el-form-item prop="password">
          <label class="form-label">密码</label>
          <el-input v-model="formData.password" type="password" placeholder="请输入密码" size="large" :prefix-icon="Lock" show-password autocomplete="current-password" />
        </el-form-item>
        <div class="login-options"><el-checkbox v-model="rememberMe">记住登录状态</el-checkbox></div>
        <el-button type="primary" size="large" :loading="loading" class="submit-btn" native-type="submit">登录</el-button>
      </el-form>
    </section>
  </main>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { DocumentChecked, Lock, User } from '@element-plus/icons-vue'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const rememberMe = ref(false)
const formData = reactive({ username: '', password: '' })
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }, { min: 6, message: '密码长度至少6位', trigger: 'blur' }],
}
const handleLogin = async () => {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
    loading.value = true
    await authStore.login(formData.username, formData.password)
    router.push('/')
  } catch {
    // 表单校验和请求错误均由组件与请求拦截器处理。
  } finally { loading.value = false }
}
</script>

<style scoped>
.login-page {
  position: relative;
  display: grid;
  min-height: 100vh;
  place-items: center;
  padding: 24px;
  overflow: hidden;
  background-color: #eef6fc;
  background-image:
    linear-gradient(rgb(148 163 184 / .10) 1px, transparent 1px),
    linear-gradient(90deg, rgb(148 163 184 / .10) 1px, transparent 1px),
    radial-gradient(circle at 50% -15%, #d7edfc 0%, rgb(215 237 252 / 0) 48%),
    linear-gradient(140deg, #f8fbff 0%, #eef6fc 56%, #e2f0fa 100%);
  background-size: 36px 36px, 36px 36px, auto, auto;
}
.login-page::before,
.login-page::after {
  position: absolute;
  width: 420px;
  height: 420px;
  border: 1px solid rgb(96 165 250 / .16);
  border-radius: 50%;
  content: '';
  pointer-events: none;
}
.login-page::before { top: -250px; right: -70px; box-shadow: 0 0 0 60px rgb(96 165 250 / .04), 0 0 0 120px rgb(96 165 250 / .03); }
.login-page::after { bottom: -310px; left: -130px; box-shadow: 0 0 0 70px rgb(96 165 250 / .035); }
.login-card { position: relative; z-index: 1; width: min(100%, 400px); padding: 44px 40px 40px; background: rgb(255 255 255 / .97); border: 1px solid rgb(203 213 225 / .72); border-radius: 16px; box-shadow: 0 20px 50px rgb(30 64 175 / .10); }
.login-header { margin-bottom: 32px; text-align: center; }
.brand-mark { display: grid; width: 46px; height: 46px; margin: 0 auto 16px; color: #1d4ed8; font-size: 23px; place-items: center; background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 14px; }
.login-header h1 { color: #0f172a; font-size: 24px; font-weight: 700; letter-spacing: .02em; }
.form-label { display: block; margin-bottom: 8px; color: #374151; font-size: 14px; font-weight: 500; }
.login-form :deep(.el-form-item) { margin-bottom: 20px; }
.login-form :deep(.el-input__wrapper) { min-height: 44px; border-radius: 10px; box-shadow: 0 0 0 1px #d1d5db inset; }
.login-form :deep(.el-input__wrapper.is-focus) { box-shadow: 0 0 0 2px #bfdbfe inset; }
.login-options { margin: -4px 0 24px; }
.submit-btn { width: 100%; height: 46px; border: 0; border-radius: 10px; background: #111827; font-weight: 600; }
.submit-btn:hover, .submit-btn:focus { background: #1f2937; }
@media (max-width: 480px) { .login-page { padding: 16px; } .login-card { padding: 36px 24px; } }
</style>
