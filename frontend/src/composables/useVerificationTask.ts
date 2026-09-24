import { computed, onScopeDispose, ref } from 'vue'
import { ApiError, createTask, getClaimDetail, getClaims, getTask, retryClaim } from '../api/client'
import type { ClaimDetail, ClaimListItem, TaskStatus, TaskSummary } from '../types/api'
import { downloadReport } from '../utils/export'

export const MAX_INPUT_LENGTH = 20_000
const POLL_INTERVAL_MS = 1_500
const POLL_TIMEOUT_MS = 180_000
/** Owns one verification session: submission, polling, details, retry and export. */
export function useVerificationTask() {
  const text = ref('')
  const task = ref<TaskSummary | null>(null)
  const taskId = ref<string | null>(null)
  const claims = ref<ClaimListItem[]>([])
  const details = ref<Record<string, ClaimDetail>>({})
  const activeDetail = ref<string | null>(null)
  const retryingClaim = ref<string | null>(null)
  const errorMessage = ref<string | null>(null)
  const formError = ref<string | null>(null)
  const phase = ref<'input' | 'processing' | 'report'>('input')
  let polling = false
  let pollStartedAt = 0
  let pollGeneration = 0

  const isTerminal = (status: TaskStatus): boolean => ['succeeded', 'partial', 'failed', 'interrupted'].includes(status)
  const canExport = computed(() => task.value?.status === 'succeeded' || task.value?.status === 'partial')

  async function exportReport(format: 'json' | 'md'): Promise<void> {
    if (!task.value) return
    try { await downloadReport(task.value.task_id, format) }
    catch (error: unknown) { errorMessage.value = explainError(error) }
  }

  function explainError(error: unknown): string {
    return error instanceof ApiError ? error.message : '发生未知错误，请稍后重试。'
  }

  async function submit(): Promise<void> {
    const input = text.value.trim()
    const generation = ++pollGeneration
    polling = false
    formError.value = null
    errorMessage.value = null
    if (!input) { formError.value = '请输入需要核验的 AI 回答。'; return }
    if (input.length > MAX_INPUT_LENGTH) { formError.value = `输入超过 ${MAX_INPUT_LENGTH.toLocaleString()} 字符上限，请删减后再试。`; return }
    try {
      phase.value = 'processing'
      const created = await createTask(input)
      if (generation !== pollGeneration) return
      taskId.value = created.task_id
      task.value = null
      claims.value = []
      details.value = {}
      pollStartedAt = Date.now()
      polling = true
      await poll(created.task_id, generation)
    } catch (error: unknown) {
      if (generation !== pollGeneration) return
      phase.value = 'input'
      errorMessage.value = explainError(error)
    }
  }

  async function poll(taskId: string, generation: number): Promise<void> {
    while (polling && generation === pollGeneration) {
      if (Date.now() - pollStartedAt > POLL_TIMEOUT_MS) {
        polling = false
        errorMessage.value = '任务处理超过 3 分钟，已停止页面刷新。后台任务可能仍在继续，可使用“恢复刷新”查看当前结果。'
        phase.value = 'report'
        return
      }
      try {
        const current = await getTask(taskId)
        if (generation !== pollGeneration) return
        task.value = current
        await refreshClaims(taskId, generation)
        if (generation !== pollGeneration) return
        if (isTerminal(current.status)) {
          polling = false
          phase.value = 'report'
          return
        }
      } catch (error: unknown) {
        if (generation !== pollGeneration) return
        polling = false
        errorMessage.value = explainError(error)
        phase.value = 'report'
        return
      }
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, POLL_INTERVAL_MS))
    }
  }

  async function refreshClaims(taskId: string, generation: number): Promise<void> {
    try {
      const page = await getClaims(taskId)
      if (generation !== pollGeneration) return
      claims.value = page.items
    } catch (error: unknown) {
      if (generation !== pollGeneration) return
      // The summary remains useful while a partially available claims endpoint recovers.
      if (!task.value || isTerminal(task.value.status)) errorMessage.value = explainError(error)
    }
  }

  function stopPolling(): void {
    polling = false
    pollGeneration += 1
    phase.value = taskId.value ? 'report' : 'input'
    errorMessage.value = '已停止页面自动刷新；后台任务可能仍在继续。可使用“恢复刷新”查看当前结果。'
  }

  async function loadDetail(claimId: string): Promise<void> {
    activeDetail.value = claimId
    if (details.value[claimId]) {
      activeDetail.value = null
      return
    }
    try {
      details.value[claimId] = await getClaimDetail(claimId)
    } catch (error: unknown) {
      errorMessage.value = explainError(error)
    } finally {
      activeDetail.value = null
    }
  }

  async function retry(claimId: string): Promise<void> {
    if (retryingClaim.value || phase.value === 'processing' || task.value?.status === 'running' || task.value?.status === 'created') return
    const generation = ++pollGeneration
    retryingClaim.value = claimId
    errorMessage.value = null
    try {
      await retryClaim(claimId)
      if (generation !== pollGeneration) return
      delete details.value[claimId]
      if (task.value) {
        polling = true
        pollStartedAt = Date.now()
        phase.value = 'processing'
        await poll(task.value.task_id, generation)
        if (generation !== pollGeneration) return
        await loadDetail(claimId)
      }
    } catch (error: unknown) {
      if (generation !== pollGeneration) return
      errorMessage.value = explainError(error)
    } finally {
      if (retryingClaim.value === claimId) retryingClaim.value = null
    }
  }

  async function resumePolling(): Promise<void> {
    if (!taskId.value || polling || (task.value && isTerminal(task.value.status))) return
    const generation = ++pollGeneration
    errorMessage.value = null
    polling = true
    pollStartedAt = Date.now()
    phase.value = 'processing'
    await poll(taskId.value, generation)
  }

  function startOver(): void {
    polling = false
    pollGeneration += 1
    taskId.value = null
    task.value = null
    claims.value = []
    details.value = {}
    errorMessage.value = null
    formError.value = null
    phase.value = 'input'
  }

  onScopeDispose(() => { polling = false; pollGeneration += 1 })

  return {
    text, taskId, task, claims, details, activeDetail, retryingClaim, errorMessage, formError,
    phase, canExport, submit, exportReport, stopPolling, resumePolling, loadDetail, retry, startOver,
  }
}
