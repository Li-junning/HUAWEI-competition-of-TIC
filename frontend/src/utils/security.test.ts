import { describe, expect, it } from 'vitest'
import { formatDate, percent, safeExternalUrl } from './security'

describe('safeExternalUrl', () => {
  it('accepts only http and https without credentials', () => {
    expect(safeExternalUrl('https://example.com/evidence')).toBe('https://example.com/evidence')
    expect(safeExternalUrl('javascript:alert(1)')).toBeNull()
    expect(safeExternalUrl('https://user:pass@example.com')).toBeNull()
    expect(safeExternalUrl('not-a-url')).toBeNull()
  })
})

describe('display helpers', () => {
  it('renders unknown date without inventing a timestamp', () => {
    expect(formatDate(null)).toBe('未知日期')
    expect(formatDate('not-a-date')).toBe('未知日期')
  })

  it('formats coverage as a percentage', () => {
    expect(percent(0.6667)).toBe('67%')
    expect(percent(null)).toBe('不适用')
  })
})
