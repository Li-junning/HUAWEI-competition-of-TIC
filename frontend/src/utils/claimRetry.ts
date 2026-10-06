import type { ClaimListItem } from '../types/api'

/** Keep budget recovery separate from unresolved references needing review. */
export function canRetryClaim(claim: ClaimListItem): boolean {
  return claim.label === 'evidence_insufficient' || claim.state === 'failed'
    || Boolean(claim.retrieval_warnings?.length)
    || (claim.state === 'unchecked' && (claim.manually_edited
      || claim.unchecked_reason === 'task_budget'
      || (claim.reason ?? '').startsWith('任务总预算已耗尽')))
}
