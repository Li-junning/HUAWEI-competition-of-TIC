import { effectScope, ref } from 'vue'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { getClaimDetail } from '../api/client'
import type { ClaimDetail, TaskSummary } from '../types/api'
import type { useVerificationTask } from './useVerificationTask'
import { useTaskNavigation } from './useTaskNavigation'

vi.mock('../api/client', async original => ({ ...await original<typeof import('../api/client')>(), getClaimDetail: vi.fn() }))
let url: URL
let scope: ReturnType<typeof effectScope>
const handlers = new Map<string, () => void>()
beforeEach(() => {
  url = new URL('http://localhost:5173/')
  vi.stubGlobal('window', {
    get location() { return { href: url.href, search: url.search } },
    history: {
      pushState: vi.fn((_state, _unused, next) => { url = new URL(String(next), url) }),
      replaceState: vi.fn((_state, _unused, next) => { url = new URL(String(next), url) }),
    },
    addEventListener: (name: string, fn: () => void) => handlers.set(name, fn),
    removeEventListener: (name: string) => handlers.delete(name), scrollTo: vi.fn(),
  })
  scope = effectScope()
})
afterEach(() => { scope.stop(); vi.unstubAllGlobals(); vi.resetAllMocks(); handlers.clear() })
function setup() {
  const taskId = ref<string | null>(null)
  const task = ref<TaskSummary | null>(null)
  const details = ref<Record<string, ClaimDetail>>({})
  const session = {
    taskId, task, details, errorMessage: ref<string | null>(null),
    restoreTask: vi.fn(async (id: string) => { taskId.value = id; task.value = { task_id: id } as TaskSummary }),
    loadDetail: vi.fn(async (id: string) => { details.value[id] = { task_id: taskId.value, claim_id: id } as ClaimDetail }),
    startOver: vi.fn(() => { taskId.value = null; task.value = null; details.value = {} }),
  }
  const navigation = scope.run(() => useTaskNavigation(session as unknown as ReturnType<typeof useVerificationTask>))!
  return { session, navigation }
}
it('restores a task and selected claim from a refreshed link', async () => {
  url.search = '?task=t_saved&claim=c_saved'
  const { session, navigation } = setup()
  await navigation.restoreLocation()
  expect(session.restoreTask).toHaveBeenCalledWith('t_saved')
  expect(session.loadDetail).toHaveBeenCalledWith('c_saved')
  expect(navigation.selectedClaimId.value).toBe('c_saved')
  navigation.returnToReport()
  expect(url.search).toBe('?task=t_saved')
  navigation.newTask()
  expect(url.search).toBe('')
  expect(session.taskId.value).toBeNull()
})
it('recovers the parent of an older claim-only link', async () => {
  url.search = '?claim=c_legacy'
  vi.mocked(getClaimDetail).mockResolvedValue({ task_id: 't_legacy' } as ClaimDetail)
  const { navigation, session } = setup()
  await navigation.restoreLocation()
  expect(session.restoreTask).toHaveBeenCalledWith('t_legacy')
  expect(url.searchParams.get('task')).toBe('t_legacy')
})
it('prevents a stale legacy-link lookup from restoring a task after new task', async () => {
  url.search = '?claim=c_legacy'
  let resolveOld!: (value: ClaimDetail) => void
  vi.mocked(getClaimDetail).mockImplementation(() => new Promise(resolve => { resolveOld = resolve }))
  const { navigation, session } = setup()
  const pending = navigation.restoreLocation()
  navigation.newTask()
  resolveOld({ task_id: 't_old' } as ClaimDetail)
  await pending
  expect(session.restoreTask).not.toHaveBeenCalled()
  expect(url.search).toBe('')
})
it('updates the task link when submission supplies an id and handles browser back', async () => {
  const { navigation, session } = setup()
  session.taskId.value = 't_created'
  expect(url.search).toBe('?task=t_created')
  navigation.openClaim('c_created')
  expect(url.searchParams.get('claim')).toBe('c_created')
  url.search = '?task=t_other'
  handlers.get('popstate')!()
  await Promise.resolve(); await Promise.resolve()
  expect(session.restoreTask).toHaveBeenCalledWith('t_other')
  expect(navigation.selectedClaimId.value).toBeNull()
})
