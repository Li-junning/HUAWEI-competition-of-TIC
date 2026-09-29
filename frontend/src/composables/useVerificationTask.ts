import { computed, onScopeDispose, ref } from 'vue'
import { ApiError, addClaim as addClaimRequest, createTask, deleteClaim as deleteClaimRequest, editClaim as editClaimRequest, getClaimDetail, getClaims, getReviewHistory, getTask, mergeClaims as mergeClaimsRequest, retryClaim, splitClaim as splitClaimRequest, undoReview as undoReviewRequest } from '../api/client'
import type { ClaimDetail, ClaimListItem, ReviewEvent, TaskStatus, TaskSummary } from '../types/api'
import { downloadReport } from '../utils/export'

export const MAX_INPUT_LENGTH = 20_000
const POLL_INTERVAL_MS = 1_500
const POLL_TIMEOUT_MS = 180_000
const GET_RETRY_DELAYS_MS = [250, 500, 1_000] as const

/** Owns one verification session: submission, polling, details, retry and export. */
export function useVerificationTask() {
  const text = ref('')
  const task = ref<TaskSummary | null>(null)
  const taskId = ref<string | null>(null)
  const claims = ref<ClaimListItem[]>([])
  const details = ref<Record<string, ClaimDetail>>({})
  const activeDetail = ref<string | null>(null)
  const retryingClaim = ref<string | null>(null)
  const removingClaim = ref<string | null>(null)
  const reviewBusy = ref(false)
  const reviewHistory = ref<ReviewEvent[]>([])
  const errorMessage = ref<string | null>(null)
  const formError = ref<string | null>(null)
  const phase = ref<'input' | 'processing' | 'report'>('input')
  let polling = false
  const pollingState = ref(false)
  let pollStartedAt = 0
  let pollGeneration = 0
  let detailRequestToken = 0
  const detailTokens = new Map<string, number>()
  const claimSignatures = new Map<string, string>()

  const isTerminal = (status: TaskStatus): boolean => ['succeeded', 'partial', 'failed', 'interrupted'].includes(status)
  const canExport = computed(() => task.value?.status === 'succeeded' || task.value?.status === 'partial')
  const canRefresh = computed(() => taskId.value !== null && !pollingState.value)

  function explainError(error: unknown): string {
    return error instanceof ApiError ? error.message : '发生未知错误，请稍后重试。'
  }

  function isTemporary(error: unknown): boolean {
    return error instanceof ApiError && (error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500)
  }

  function setPolling(active: boolean): void {
    polling = active
    pollingState.value = active
  }

  class StaleRequestError extends Error {}

  async function getWithRetry<T>(request: () => Promise<T>, isCurrent: () => boolean = () => true): Promise<T> {
    for (let attempt = 0; ; attempt += 1) {
      if (!isCurrent()) throw new StaleRequestError()
      try { return await request() }
      catch (error: unknown) {
        if (!isCurrent()) throw new StaleRequestError()
        const delay = GET_RETRY_DELAYS_MS[attempt]
        if (!isTemporary(error) || delay === undefined) throw error
        await new Promise<void>((resolve) => globalThis.setTimeout(resolve, delay))
      }
    }
  }

  function isTaskCurrent(id: string, generation: number): boolean {
    return id === taskId.value && generation === pollGeneration
  }

  async function exportReport(format: 'json' | 'md'): Promise<void> {
    const id = taskId.value
    if (!id) return
    try { await downloadReport(id, format) }
    catch (error: unknown) { if (id === taskId.value) errorMessage.value = explainError(error) }
  }

  async function submit(): Promise<void> {
    const input = text.value.trim()
    const generation = ++pollGeneration
    setPolling(false)
    formError.value = null
    errorMessage.value = null
    if (!input) { formError.value = '请输入需要核验的 AI 回答。'; return }
    if (input.length > MAX_INPUT_LENGTH) { formError.value = `输入超过 ${MAX_INPUT_LENGTH.toLocaleString()} 字符上限，请删减后再试。`; return }
    phase.value = 'processing'
    try {
      // Creation is a POST and must never be automatically retried.
      const created = await createTask(input)
      if (generation !== pollGeneration) return
      resetForTask(created.task_id)
      pollStartedAt = Date.now()
      setPolling(true)
      await poll(created.task_id, generation)
    } catch (error: unknown) {
      if (generation !== pollGeneration) return
      phase.value = 'input'
      errorMessage.value = explainError(error)
    }
  }

  function resetForTask(id: string): void {
    taskId.value = id
    task.value = null
    claims.value = []
    details.value = {}
    activeDetail.value = null
    detailRequestToken += 1
    detailTokens.clear()
    claimSignatures.clear()
    retryingClaim.value = null
    reviewHistory.value = []
  }

  async function poll(id: string, generation: number): Promise<void> {
    while (polling && generation === pollGeneration && id === taskId.value) {
      if (Date.now() - pollStartedAt > POLL_TIMEOUT_MS) {
        setPolling(false)
        errorMessage.value = '任务处理超过 3 分钟，已停止页面刷新。后台任务可能仍在继续，可使用“恢复刷新”查看当前结果。'
        phase.value = 'report'
        return
      }
      try {
        const current = await getWithRetry(() => getTask(id), () => isTaskCurrent(id, generation))
        if (generation !== pollGeneration || id !== taskId.value) return
        task.value = current
        await refreshClaims(id, generation)
        if (generation !== pollGeneration || id !== taskId.value) return
        if (isTerminal(current.status)) {
          setPolling(false)
          phase.value = 'report'
          void loadReviewHistory(id)
          return
        }
      } catch (error: unknown) {
        if (generation !== pollGeneration || id !== taskId.value) return
        setPolling(false)
        errorMessage.value = explainError(error)
        phase.value = 'report'
        return
      }
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, POLL_INTERVAL_MS))
    }
  }

  async function refreshClaims(id: string, generation: number): Promise<void> {
    try {
      const page = await getWithRetry(() => getClaims(id), () => isTaskCurrent(id, generation))
      if (generation !== pollGeneration || id !== taskId.value) return
      const previous = details.value
      claims.value = page.items
      const next = { ...previous }
      const changedDetails: string[] = []
      for (const item of page.items) {
        const signature = JSON.stringify([item.state, item.evidence_cluster_ids])
        const oldSignature = claimSignatures.get(item.claim_id)
        claimSignatures.set(item.claim_id, signature)
        const detail = next[item.claim_id]
        const detailChanged = Boolean(detail && (
          detail.state !== item.state
          || JSON.stringify(detail.evidence_cluster_ids) !== JSON.stringify(item.evidence_cluster_ids)
        ))
        if ((oldSignature !== undefined && oldSignature !== signature) || detailChanged) {
          const shouldReload = Boolean(detail) || activeDetail.value === item.claim_id
          delete next[item.claim_id]
          // Invalidate any older detail GET before requesting the latest evidence body.
          detailTokens.set(item.claim_id, ++detailRequestToken)
          if (shouldReload) changedDetails.push(item.claim_id)
        } else if (detail) {
          next[item.claim_id] = { ...detail, ...item }
        }
      }
      details.value = next
      for (const claimId of changedDetails) void loadDetail(claimId)
    } catch (error: unknown) {
      if (generation !== pollGeneration || id !== taskId.value) return
      if (!task.value || isTerminal(task.value.status)) errorMessage.value = explainError(error)
    }
  }

  function stopPolling(): void {
    setPolling(false)
    pollGeneration += 1
    phase.value = taskId.value ? 'report' : 'input'
    errorMessage.value = '已停止页面自动刷新；后台任务可能仍在继续。可使用“恢复刷新”查看当前结果。'
  }

  async function loadDetail(claimId: string): Promise<void> {
    const generation = pollGeneration
    const id = taskId.value
    const token = ++detailRequestToken
    detailTokens.set(claimId, token)
    activeDetail.value = claimId
    if (details.value[claimId]) {
      if (detailTokens.get(claimId) === token) activeDetail.value = null
      return
    }
    try {
      const detail = await getWithRetry(() => getClaimDetail(claimId),
        () => isTaskCurrent(id ?? '', generation) && detailTokens.get(claimId) === token)
      if (!isTaskCurrent(id ?? '', generation) || detailTokens.get(claimId) !== token) return
      if (detail.task_id !== id) {
        errorMessage.value = '返回的核验详情与当前任务不匹配，请刷新任务后重试。'
        return
      }
      details.value = { ...details.value, [claimId]: detail }
    } catch (error: unknown) {
      if (isTaskCurrent(id ?? '', generation) && detailTokens.get(claimId) === token) errorMessage.value = explainError(error)
    } finally {
      if (detailTokens.get(claimId) === token && activeDetail.value === claimId) activeDetail.value = null
    }
  }

  async function retry(claimId: string): Promise<void> {
    if (retryingClaim.value || phase.value === 'processing' || task.value?.status === 'running' || task.value?.status === 'created') return
    const generation = ++pollGeneration
    const id = taskId.value
    retryingClaim.value = claimId
    errorMessage.value = null
    try {
      // Explicit user action: this POST is issued once, without automatic retry.
      await retryClaim(claimId)
      if (generation !== pollGeneration || id !== taskId.value) return
      const next = { ...details.value }
      delete next[claimId]
      details.value = next
      if (id && task.value) {
        setPolling(true)
        pollStartedAt = Date.now()
        phase.value = 'processing'
        await poll(id, generation)
        if (generation !== pollGeneration || id !== taskId.value) return
        await loadDetail(claimId)
      }
    } catch (error: unknown) {
      if (generation === pollGeneration && id === taskId.value) errorMessage.value = explainError(error)
    } finally {
      if (retryingClaim.value === claimId) retryingClaim.value = null
    }
  }

  async function refreshAfterReviewChange(id: string, generation: number): Promise<void> {
    const [current, page, history] = await Promise.all([getTask(id), getClaims(id), getReviewHistory(id)])
    if (!isTaskCurrent(id, generation)) return
    task.value = current
    claims.value = page.items
    reviewHistory.value = history.items
    details.value = {}
    claimSignatures.clear()
    for (const item of page.items) claimSignatures.set(item.claim_id, JSON.stringify([item.state, item.evidence_cluster_ids]))
  }

  async function loadReviewHistory(id: string): Promise<void> {
    try {
      const history = await getReviewHistory(id)
      if (id === taskId.value) reviewHistory.value = history.items
    } catch (error: unknown) { if (id === taskId.value) errorMessage.value = explainError(error) }
  }

  async function runReview(operation: (id: string) => Promise<unknown>): Promise<boolean> {
    const id = taskId.value
    if (!id || reviewBusy.value || task.value?.status === 'running' || task.value?.status === 'created') return false
    const generation = pollGeneration
    reviewBusy.value = true
    errorMessage.value = null
    try {
      await operation(id)
      await refreshAfterReviewChange(id, generation)
      return isTaskCurrent(id, generation)
    } catch (error: unknown) { errorMessage.value = explainError(error); return false }
    finally { reviewBusy.value = false }
  }

  async function editClaimText(claimId: string, normalizedClaim: string, reviewer: string): Promise<void> {
    if (!normalizedClaim.trim()) return
    const changed = await runReview(() => editClaimRequest(claimId, normalizedClaim.trim(), reviewer))
    if (changed && taskId.value) await loadDetail(claimId)
  }

  async function removeClaim(claimId: string, reviewer: string): Promise<void> {
    const id = taskId.value
    if (!id || removingClaim.value) return
    removingClaim.value = claimId
    try { await runReview(() => deleteClaimRequest(claimId, reviewer)) }
    finally { if (removingClaim.value === claimId) removingClaim.value = null }
  }

  async function addManualClaim(start: number, end: number, wording: string, reviewer: string): Promise<boolean> {
    return runReview(id => addClaimRequest(id, start, end, wording, reviewer))
  }

  async function splitManualClaim(claimId: string, offset: number, first: string, second: string, reviewer: string): Promise<boolean> {
    return runReview(() => splitClaimRequest(claimId, offset, first, second, reviewer))
  }

  async function mergeManualClaims(ids: string[], wording: string, reviewer: string): Promise<boolean> {
    return runReview(id => mergeClaimsRequest(id, ids, wording, reviewer))
  }

  async function undoReviewEvent(eventId: string, reviewer: string): Promise<boolean> {
    return runReview(id => undoReviewRequest(id, eventId, reviewer))
  }

  async function resumePolling(): Promise<void> {
    const id = taskId.value
    if (!id || polling) return
    const generation = ++pollGeneration
    errorMessage.value = null
    setPolling(true)
    pollStartedAt = Date.now()
    phase.value = task.value && isTerminal(task.value.status) ? 'report' : 'processing'
    await poll(id, generation)
  }

  /** Loads a task and its claims, then continues polling in the background if it is active. */
  async function restoreTask(id: string): Promise<void> {
    const generation = ++pollGeneration
    setPolling(false)
    errorMessage.value = null
    resetForTask(id)
    phase.value = 'processing'
    try {
      const current = await getWithRetry(() => getTask(id), () => isTaskCurrent(id, generation))
      if (generation !== pollGeneration || id !== taskId.value) return
      task.value = current
      await refreshClaims(id, generation)
      if (generation !== pollGeneration || id !== taskId.value) return
      if (isTerminal(current.status)) { phase.value = 'report'; void loadReviewHistory(id) }
      else {
        phase.value = 'processing'
        pollStartedAt = Date.now()
        setPolling(true)
        void poll(id, generation)
      }
    } catch (error: unknown) {
      if (generation !== pollGeneration || id !== taskId.value) return
      phase.value = 'report'
      errorMessage.value = explainError(error)
    }
  }

  function startOver(): void {
    setPolling(false)
    pollGeneration += 1
    text.value = ''
    taskId.value = null
    task.value = null
    claims.value = []
    details.value = {}
    activeDetail.value = null
    detailRequestToken += 1
    detailTokens.clear()
    claimSignatures.clear()
    retryingClaim.value = null
    reviewHistory.value = []
    errorMessage.value = null
    formError.value = null
    phase.value = 'input'
  }

  onScopeDispose(() => { setPolling(false); pollGeneration += 1 })

  return {
    text, taskId, task, claims, details, activeDetail, retryingClaim, removingClaim, reviewBusy, reviewHistory, errorMessage, formError,
    phase, canExport, canRefresh, submit, exportReport, stopPolling, resumePolling, loadDetail, retry, editClaimText, removeClaim,
    addManualClaim, splitManualClaim, mergeManualClaims, undoReviewEvent, restoreTask, startOver,
  }
}
