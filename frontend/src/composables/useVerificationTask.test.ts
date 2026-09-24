import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { downloadReport } from '../utils/export'
import type { ClaimDetail, TaskSummary } from '../types/api'
import { useVerificationTask } from './useVerificationTask'
import { useClaimFilters } from './useClaimFilters'

vi.mock('../api/client', async (original) => ({
  ...await original<typeof import('../api/client')>(),
  createTask: vi.fn(), getTask: vi.fn(), getClaims: vi.fn(),
  getClaimDetail: vi.fn(), retryClaim: vi.fn(),
}))
vi.mock('../utils/export', () => ({ downloadReport: vi.fn() }))

const summary: TaskSummary = {
  task_id: 't_test', status: 'succeeded', created_at: '', updated_at: '',
  input_char_count: 7, claim_limit: 15, claims_extracted: 1, claims_processed: 1,
  claims_unchecked: 0, truncated: false, failed_providers: [],
  coverage: { verifiable_claims: 1, processed_verifiable_claims: 1,
    adjudicated_verifiable_claims: 0, processing_coverage: 1,
    verification_coverage: 0, extraction_coverage: null },
  label_counts: { credible: 0, disputed: 0, incorrect: 0, evidence_insufficient: 1, not_applicable: 0 },
  score: null, score_note: null, error_code: null,
}
const claim: ClaimDetail = {
  claim_id: 'c_test', task_id: 't_test', source_text: '某机构发布报告。',
  normalized_claim: '某机构发布报告。', char_start: 0, char_end: 8, type: 'general',
  entities: [], conditions: [], queries: [], label: 'evidence_insufficient',
  support_score: null, reason: null, state: 'done', retry_count: 0,
  evidence_cluster_ids: [], evidence_clusters: [],
}

describe('verification session', () => {
  let scope: ReturnType<typeof effectScope>
  let session: ReturnType<typeof useVerificationTask>

  beforeEach(() => {
    vi.resetAllMocks()
    vi.useFakeTimers()
    vi.mocked(api.createTask).mockResolvedValue({ task_id: 't_test', status: 'created' })
    vi.mocked(api.getTask).mockResolvedValue(summary)
    vi.mocked(api.getClaims).mockResolvedValue({ items: [claim], total: 1, offset: 0, limit: 20 })
    vi.mocked(api.getClaimDetail).mockResolvedValue(claim)
    vi.mocked(api.retryClaim).mockResolvedValue({ claim_id: 'c_test', state: 'retrieving' })
    scope = effectScope()
    session = scope.run(useVerificationTask)!
    session.text.value = '  某机构发布报告。  '
  })

  afterEach(() => {
    scope.stop()
    vi.useRealTimers()
  })

  it('rejects blank and oversized input before creating a task', async () => {
    session.text.value = '  '
    await session.submit()
    expect(session.formError.value).toContain('请输入')
    session.text.value = '文'.repeat(20_001)
    await session.submit()
    expect(session.formError.value).toContain('上限')
    expect(api.createTask).not.toHaveBeenCalled()
    expect(session.phase.value).toBe('input')
  })

  it('polls to completion, displays claims and exports the report', async () => {
    vi.mocked(api.getTask).mockResolvedValueOnce({ ...summary, status: 'running' })
    const pending = session.submit()
    await vi.advanceTimersByTimeAsync(0)
    expect(session.phase.value).toBe('processing')
    await vi.advanceTimersByTimeAsync(1500)
    await pending
    expect(api.createTask).toHaveBeenCalledWith('某机构发布报告。')
    expect(session.phase.value).toBe('report')
    expect(session.claims.value).toEqual([claim])
    expect(session.canExport.value).toBe(true)
    await session.exportReport('md')
    expect(downloadReport).toHaveBeenCalledWith('t_test', 'md')
  })

  it('invalidates cached details after retry and loads the new result', async () => {
    await session.submit()
    await session.loadDetail('c_test')
    await session.loadDetail('c_test')
    expect(api.getClaimDetail).toHaveBeenCalledTimes(1)
    vi.mocked(api.getClaimDetail).mockResolvedValue({ ...claim, retry_count: 1 })
    await session.retry('c_test')
    expect(api.retryClaim).toHaveBeenCalledWith('c_test')
    expect(api.getClaimDetail).toHaveBeenCalledTimes(2)
    expect(session.details.value.c_test?.retry_count).toBe(1)
    expect(session.retryingClaim.value).toBeNull()
    expect(session.phase.value).toBe('report')
  })

  it('retains a completed summary if loading its claims fails', async () => {
    vi.mocked(api.getClaims).mockRejectedValue(new api.ApiError('暂时不可用', 503))
    await session.submit()
    expect(session.task.value).toEqual(summary)
    expect(session.phase.value).toBe('report')
    expect(session.errorMessage.value).toBe('暂时不可用')
  })

  it('keeps a created task available for refresh when its first status request fails', async () => {
    vi.mocked(api.getTask).mockRejectedValue(new api.ApiError('连接失败', 0))
    await session.submit()
    expect(session.phase.value).toBe('report')
    expect(session.taskId.value).toBe('t_test')
    expect(session.errorMessage.value).toBe('连接失败')
    vi.mocked(api.getTask).mockResolvedValue(summary)
    await session.resumePolling()
    expect(session.task.value).toEqual(summary)
    expect(session.phase.value).toBe('report')
  })

  it('offers exports only for server exportable statuses', async () => {
    await session.submit()
    session.task.value = { ...summary, status: 'failed' }
    expect(session.canExport.value).toBe(false)
    session.task.value = { ...summary, status: 'interrupted' }
    expect(session.canExport.value).toBe(false)
    session.task.value = { ...summary, status: 'partial' }
    expect(session.canExport.value).toBe(true)
  })

  it('stops polling at the existing three-minute budget', async () => {
    vi.mocked(api.getTask).mockResolvedValue({ ...summary, status: 'running' })
    const pending = session.submit()
    await vi.advanceTimersByTimeAsync(181_500)
    await pending
    const calls = vi.mocked(api.getTask).mock.calls.length
    await vi.advanceTimersByTimeAsync(3000)
    expect(api.getTask).toHaveBeenCalledTimes(calls)
    expect(session.phase.value).toBe('report')
    expect(session.errorMessage.value).toContain('超过 3 分钟')
  })

  it.each(['stop', 'dispose'] as const)('stops future polling on %s', async (action) => {
    vi.mocked(api.getTask).mockResolvedValue({ ...summary, status: 'running' })
    const pending = session.submit()
    await vi.advanceTimersByTimeAsync(0)
    if (action === 'stop') session.stopPolling()
    else scope.stop()
    await vi.advanceTimersByTimeAsync(3000)
    await pending
    expect(api.getTask).toHaveBeenCalledTimes(1)
  })

  it('does not let a stopped poll resume after a newer refresh starts', async () => {
    vi.mocked(api.getTask)
      .mockResolvedValueOnce({ ...summary, status: 'running' })
      .mockResolvedValueOnce(summary)
    const firstPoll = session.submit()
    await vi.advanceTimersByTimeAsync(0)
    session.stopPolling()
    await session.resumePolling()
    await vi.advanceTimersByTimeAsync(1_500)
    await firstPoll
    expect(api.getTask).toHaveBeenCalledTimes(2)
    expect(session.task.value?.status).toBe('succeeded')
  })

  it('filters and sorts without changing the source claim order', async () => {
    await session.submit()
    session.claims.value = [claim, { ...claim, claim_id: 'c_risk', label: 'incorrect' }]
    const filters = useClaimFilters(session.claims)
    filters.sortRisk.value = true
    expect(filters.filteredClaims.value.map(item => item.claim_id)).toEqual(['c_risk', 'c_test'])
    expect(session.claims.value.map(item => item.claim_id)).toEqual(['c_test', 'c_risk'])
    filters.filter.value = 'evidence_insufficient'
    expect(filters.filteredClaims.value).toEqual([claim])
    session.startOver()
    expect(filters.filteredClaims.value).toEqual([])
    expect(session.task.value).toBeNull()
    expect(session.phase.value).toBe('input')
  })
})
