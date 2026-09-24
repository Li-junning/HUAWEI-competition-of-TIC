/** Return a link only when the backend supplied a safe, external HTTP(S) URL. */
export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const parsed = new URL(value)
    if ((parsed.protocol !== 'http:' && parsed.protocol !== 'https:') || parsed.username || parsed.password) return null
    return parsed.toString()
  } catch {
    return null
  }
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return '未知日期'
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? '未知日期' : parsed.toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' })
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '不适用' : `${Math.round(value * 100)}%`
}
