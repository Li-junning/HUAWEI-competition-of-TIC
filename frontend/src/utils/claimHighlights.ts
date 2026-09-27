import type { ClaimListItem } from '../types/api'

export interface ClaimTextSegment {
  text: string
  claimIds: string[]
}

function splitsSurrogatePair(text: string, offset: number): boolean {
  if (offset <= 0 || offset >= text.length) return false
  const previous = text.charCodeAt(offset - 1)
  const next = text.charCodeAt(offset)
  return previous >= 0xd800 && previous <= 0xdbff && next >= 0xdc00 && next <= 0xdfff
}

/** Segment exact, non-overlapping source ranges using UTF-16 half-open offsets. */
export function segmentClaimText(text: string, claims: ClaimListItem[]): ClaimTextSegment[] {
  const candidates = claims.filter(claim =>
    Number.isInteger(claim.char_start) && Number.isInteger(claim.char_end)
    && claim.char_start >= 0 && claim.char_end > claim.char_start && claim.char_end <= text.length
    && !splitsSurrogatePair(text, claim.char_start) && !splitsSurrogatePair(text, claim.char_end)
    && text.slice(claim.char_start, claim.char_end) === claim.source_text,
  )
  const conflicts = new Set<string>()
  for (let i = 0; i < candidates.length; i += 1) {
    for (let j = i + 1; j < candidates.length; j += 1) {
      if (candidates[i].char_start < candidates[j].char_end && candidates[j].char_start < candidates[i].char_end) {
        conflicts.add(candidates[i].claim_id)
        conflicts.add(candidates[j].claim_id)
      }
    }
  }
  const ranges = candidates.filter(claim => !conflicts.has(claim.claim_id))
  const boundaries = new Set<number>([0, text.length])
  for (const claim of ranges) {
    boundaries.add(claim.char_start)
    boundaries.add(claim.char_end)
  }
  const points = [...boundaries].sort((a, b) => a - b)
  const segments: ClaimTextSegment[] = []
  for (let i = 0; i < points.length - 1; i += 1) {
    const start = points[i]
    const end = points[i + 1]
    const claimIds = ranges.filter(claim => claim.char_start <= start && claim.char_end >= end).map(claim => claim.claim_id)
    const value = text.slice(start, end)
    const previous = segments[segments.length - 1]
    if (previous && previous.claimIds.join('\0') === claimIds.join('\0')) previous.text += value
    else segments.push({ text: value, claimIds })
  }
  return segments
}