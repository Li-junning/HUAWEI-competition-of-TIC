<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import App from './App.vue'
import { getAuthSession, loginWorkspace, logoutWorkspace, setCsrfToken } from './api/client'

const ready = ref(false)
const authenticated = ref(false)
const required = ref(true)
const password = ref('')
const busy = ref(false)
const error = ref('')
function lock(): void { authenticated.value = false; setCsrfToken(null); password.value = '' }
async function check(): Promise<void> {
  busy.value = true; error.value = ''
  try {
    const session = await getAuthSession()
    if (typeof session.authenticated !== 'boolean' || typeof session.required !== 'boolean') throw new Error('登录服务返回异常，请检查服务配置。')
    required.value = session.required; authenticated.value = session.authenticated
    setCsrfToken(session.csrf_token); ready.value = true
  } catch (err) { error.value = err instanceof Error ? err.message : '无法连接服务' }
  finally { busy.value = false }
}
async function login(): Promise<void> {
  busy.value = true; error.value = ''
  try {
    const session = await loginWorkspace(password.value)
    if (!session.authenticated || !session.csrf_token) throw new Error('登录服务返回异常，请重试。')
    setCsrfToken(session.csrf_token); authenticated.value = true; password.value = ''
  } catch (err) { error.value = err instanceof Error ? err.message : '登录失败' }
  finally { busy.value = false }
}
async function logout(): Promise<void> {
  busy.value = true; error.value = ''
  try { await logoutWorkspace(); lock() }
  catch (err) { error.value = err instanceof Error ? err.message : '退出失败，请重试' }
  finally { busy.value = false }
}
onMounted(() => { window.addEventListener('verifier:auth-required', lock); void check() })
onUnmounted(() => window.removeEventListener('verifier:auth-required', lock))
</script>

<template>
  <template v-if="ready && authenticated">
    <App />
    <div v-if="required" class="session-controls"><span v-if="error" role="alert">{{ error }}</span><button class="secondary-button" :disabled="busy" @click="logout">退出工作区</button></div>
  </template>
  <main v-else class="access-screen">
    <form class="access-card" @submit.prevent="ready ? login() : check()">
      <p class="eyebrow">EVIDENCE WORKSPACE</p><h1>可信度验证台</h1>
      <template v-if="ready">
        <p>输入访问密码以进入工作区。仅向受信任的人分享密码。</p>
        <label for="workspace-password">访问密码</label>
        <input id="workspace-password" v-model="password" type="password" autocomplete="current-password" maxlength="1024" required :disabled="busy" />
      </template>
      <p v-else>正在连接工作区…</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <button class="primary-button" type="submit" :disabled="busy">{{ busy ? '请稍候…' : ready ? '登录' : '重新连接' }}</button>
    </form>
  </main>
</template>

<style scoped>
.access-screen { min-height: 100vh; display: grid; place-items: center; padding: 24px; background: #f5f7fb; }
.access-card { width: min(440px, 100%); padding: 36px; background: white; border: 1px solid #dce2ec; border-radius: 20px; box-shadow: 0 12px 40px #10234b0d; }
.access-card h1 { font-size: 28px; margin: 12px 0; }
.access-card p { line-height: 1.7; }
.access-card label { display: block; margin-top: 24px; }
.access-card input { box-sizing: border-box; width: 100%; padding: 12px; border: 1px solid #bcc6d8; border-radius: 8px; margin: 8px 0 24px; font: inherit; }
.access-card button { width: 100%; justify-content: center; }
.session-controls { position: fixed; right: 20px; bottom: 20px; z-index: 40; display: flex; gap: 12px; align-items: center; }
@media print { .session-controls { display: none; } }
</style>
