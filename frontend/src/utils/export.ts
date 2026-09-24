import { ApiError } from '../api/client'

/** Download the complete, server-sanitized report, including unopened claims. */
export async function downloadReport(taskId: string, format: 'json' | 'md'): Promise<void> {
  const response = await fetch(`/api/tasks/${encodeURIComponent(taskId)}/export?format=${format}`, {
    headers: { Accept: format === 'json' ? 'application/json' : 'text/markdown' },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { error?: { message?: string; code?: string } } | null
    throw new ApiError(body?.error?.message ?? `导出失败（${response.status}）`, response.status, body?.error?.code ?? null)
  }
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `verification-${taskId}.${format === 'json' ? 'json' : 'md'}`
  anchor.click()
  URL.revokeObjectURL(url)
}
