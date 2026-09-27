import { onScopeDispose, ref, watch, type Ref } from 'vue'
import { ApiError, getTaskInput } from '../api/client'

/** Load original text separately; a report remains usable if this request fails. */
export function useTaskSource(taskId: Ref<string | null>) {
  const sourceText = ref('')
  const sourceError = ref<string | null>(null)
  const sourceLoading = ref(false)
  let generation = 0

  async function reloadSource(): Promise<void> {
    const id = taskId.value
    const request = ++generation
    sourceText.value = ''
    sourceError.value = null
    sourceLoading.value = !!id
    if (!id) return
    try {
      const result = await getTaskInput(id)
      if (request !== generation) return
      if (result.task_id !== id) throw new Error('Task input mismatch')
      sourceText.value = result.input_text
    } catch (error: unknown) {
      if (request !== generation) return
      sourceError.value = error instanceof ApiError ? error.message : '暂时无法加载原文。'
    } finally {
      if (request === generation) sourceLoading.value = false
    }
  }
  watch(taskId, () => { void reloadSource() }, { immediate: true, flush: 'sync' })
  onScopeDispose(() => { generation += 1 })
  return { sourceText, sourceError, sourceLoading, reloadSource }
}
