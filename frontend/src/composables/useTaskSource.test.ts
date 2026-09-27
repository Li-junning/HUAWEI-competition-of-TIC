import { effectScope, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { getTaskInput } from '../api/client'
import { useTaskSource } from './useTaskSource'

vi.mock('../api/client', async original => ({ ...await original<typeof import('../api/client')>(), getTaskInput: vi.fn() }))
const scopes: ReturnType<typeof effectScope>[] = []
afterEach(() => { scopes.forEach(scope => scope.stop()); scopes.length = 0; vi.resetAllMocks() })
const flush = async () => { await Promise.resolve(); await Promise.resolve() }

it('ignores original text arriving from a previous task', async () => {
  let resolveOld!: (value: { task_id: string; input_text: string }) => void
  vi.mocked(getTaskInput).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    .mockResolvedValueOnce({ task_id: 't_new', input_text: '新原文' })
  const taskId = ref<string | null>('t_old')
  const scope = effectScope(); scopes.push(scope)
  const source = scope.run(() => useTaskSource(taskId))!
  taskId.value = 't_new'
  await flush()
  resolveOld({ task_id: 't_old', input_text: '旧原文' })
  await flush()
  expect(source.sourceText.value).toBe('新原文')
  taskId.value = null
  expect(source.sourceText.value).toBe('')
})

it('allows retry after failure without affecting report state', async () => {
  vi.mocked(getTaskInput).mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce({ task_id: 't_one', input_text: '😀保留原文\n内容' })
  const scope = effectScope(); scopes.push(scope)
  const source = scope.run(() => useTaskSource(ref('t_one')))!
  await flush()
  expect(source.sourceError.value).toBeTruthy()
  await source.reloadSource()
  expect(source.sourceText.value).toBe('😀保留原文\n内容')
  expect(source.sourceError.value).toBeNull()
  expect(source.sourceLoading.value).toBe(false)
})
