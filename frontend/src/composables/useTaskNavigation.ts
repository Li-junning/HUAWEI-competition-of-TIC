import { onScopeDispose, ref, watch } from 'vue'
import { getClaimDetail } from '../api/client'
import type { useVerificationTask } from './useVerificationTask'

/** Keep task and claim links restorable without persisting user text in the browser. */
export function useTaskNavigation(session: ReturnType<typeof useVerificationTask>) {
  const selectedClaimId = ref<string | null>(null)
  let generation = 0
  let restoring = false

  function writeLocation(push = false): void {
    const url = new URL(window.location.href)
    if (session.taskId.value) url.searchParams.set('task', session.taskId.value)
    else url.searchParams.delete('task')
    if (selectedClaimId.value) url.searchParams.set('claim', selectedClaimId.value)
    else url.searchParams.delete('claim')
    if (url.href !== window.location.href) {
      window.history[push ? 'pushState' : 'replaceState']({ taskId: session.taskId.value }, '', url)
    }
  }

  watch(session.taskId, () => { if (!restoring) writeLocation() }, { flush: 'sync' })

  async function restoreLocation(): Promise<void> {
    const request = ++generation
    restoring = true
    const params = new URLSearchParams(window.location.search)
    let id = params.get('task')
    const claimId = params.get('claim')
    selectedClaimId.value = claimId
    session.errorMessage.value = null
    try {
      // Older saved links contained only a claim; recover its parent task once.
      if (!id && claimId) id = (await getClaimDetail(claimId)).task_id
      if (request !== generation) return
      if (!id) {
        session.startOver()
        return
      }
      if (session.taskId.value !== id || !session.task.value) await session.restoreTask(id)
      if (request !== generation) return
      if (claimId) await session.loadDetail(claimId)
      if (request !== generation) return
      const detail = claimId ? session.details.value[claimId] : null
      if (detail && detail.task_id !== id) {
        selectedClaimId.value = null
        session.errorMessage.value = '这条声明不属于当前任务，已返回任务报告。'
      }
      writeLocation()
    } catch {
      if (request === generation) session.errorMessage.value = '无法恢复此链接，请检查任务或声明是否仍然存在。'
    } finally {
      if (request === generation) restoring = false
    }
  }

  function openClaim(claimId: string): void {
    generation += 1
    restoring = false
    selectedClaimId.value = claimId
    session.errorMessage.value = null
    writeLocation(true)
    void session.loadDetail(claimId)
    window.scrollTo(0, 0)
  }

  function returnToReport(): void {
    generation += 1
    restoring = false
    selectedClaimId.value = null
    session.errorMessage.value = null
    writeLocation(true)
    window.scrollTo(0, 0)
  }

  function newTask(): void {
    generation += 1
    restoring = true
    selectedClaimId.value = null
    session.startOver()
    writeLocation(true)
    restoring = false
  }

  const onPopState = () => { void restoreLocation() }
  window.addEventListener('popstate', onPopState)
  onScopeDispose(() => {
    generation += 1
    window.removeEventListener('popstate', onPopState)
  })
  return { selectedClaimId, restoreLocation, openClaim, returnToReport, newTask }
}
