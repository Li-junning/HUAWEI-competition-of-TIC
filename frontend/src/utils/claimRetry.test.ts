import { describe, expect, it } from 'vitest'
import type { ClaimListItem } from '../types/api'
import { canRetryClaim } from './claimRetry'

const claim = (overrides: Partial<ClaimListItem>): ClaimListItem => ({
  claim_id: 'c', task_id: 't', source_text: '事实', normalized_claim: '事实', char_start: 0, char_end: 2,
  type: 'general', manually_edited: false, entities: [], conditions: [], queries: [], label: null,
  support_score: null, reason: null, state: 'done', retry_count: 0, evidence_cluster_ids: [], ...overrides,
})

describe('claim recovery', () => {
  it('allows unfinished searches even when the obtained evidence has a verdict', () => {
    expect(canRetryClaim(claim({ label: 'credible', retrieval_warnings: ['SEARCH_TIMEOUT'] }))).toBe(true)
    expect(canRetryClaim(claim({ label: 'credible' }))).toBe(false)
  })
  it('allows budget recovery and old budget reports', () => {
    expect(canRetryClaim(claim({ state: 'unchecked', unchecked_reason: 'task_budget' }))).toBe(true)
    expect(canRetryClaim(claim({ state: 'unchecked', reason: '任务总预算已耗尽，该声明尚未执行检索判断。' }))).toBe(true)
  })
  it('requires manual review for unresolved references', () => {
    expect(canRetryClaim(claim({ state: 'unchecked', unchecked_reason: 'unresolved_reference' }))).toBe(false)
    expect(canRetryClaim(claim({ state: 'unchecked', manually_edited: true }))).toBe(true)
  })
  it('continues to allow failed and insufficient claims', () => {
    expect(canRetryClaim(claim({ state: 'failed' }))).toBe(true)
    expect(canRetryClaim(claim({ label: 'evidence_insufficient' }))).toBe(true)
    expect(canRetryClaim(claim({ state: 'unchecked' }))).toBe(false)
  })
})
