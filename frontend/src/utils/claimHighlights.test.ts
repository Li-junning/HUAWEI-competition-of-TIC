import { describe, expect, it } from 'vitest'
import type { ClaimListItem } from '../types/api'
import { segmentClaimText } from './claimHighlights'

function claim(id: string, source: string, start: number, end: number): ClaimListItem {
  return {
    claim_id: id, task_id: 'task', source_text: source, char_start: start, char_end: end,
    type: 'general', normalized_claim: id, entities: [], conditions: [], queries: [],
    label: null, support_score: null, reason: null, state: 'pending', retry_count: 0, evidence_cluster_ids: [],
  }
}

describe('segmentClaimText', () => {
  it('uses UTF-16 offsets and preserves text and whitespace', () => {
    const source = '甲\n😀乙 '
    const segments = segmentClaimText(source, [claim('a', '\n😀', 1, 4)])
    expect(segments.map(segment => segment.text).join('')).toBe(source)
    expect(segments).toEqual([
      { text: '甲', claimIds: [] }, { text: '\n😀', claimIds: ['a'] }, { text: '乙 ', claimIds: [] },
    ])
  })

  it('skips overlapping, out-of-bounds, mismatched, and surrogate-splitting ranges', () => {
    const source = 'A😀BCD'
    const claims = [claim('a', '😀B', 1, 4), claim('overlap', 'BC', 3, 5), claim('outside', 'X', 6, 8), claim('split', '😀', 2, 3), claim('wrong-text', 'ZZ', 4, 6)]
    const segments = segmentClaimText(source, claims)
    expect(segments.map(segment => segment.text).join('')).toBe(source)
    expect(segments.every(segment => segment.claimIds.length === 0)).toBe(true)
  })

  it('does not highlight a legal same-length range when its source text differs', () => {
    const source = '甲乙丙'
    const segments = segmentClaimText(source, [claim('mismatch', '甲丁', 0, 2)])
    expect(segments).toEqual([{ text: source, claimIds: [] }])
  })
})