import { exportUrl, requestBlob } from '../api/client'

/** Download the complete, server-sanitized report, including unopened claims. */
export async function downloadReport(taskId: string, format: 'json' | 'md'): Promise<void> {
  const blob = await requestBlob(exportUrl(taskId, format), {
    headers: { Accept: format === 'json' ? 'application/json' : 'text/markdown' },
  })
  const url = URL.createObjectURL(blob)
  try {
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `verification-${taskId}.${format === 'json' ? 'json' : 'md'}`
    anchor.click()
  } finally {
    URL.revokeObjectURL(url)
  }
}
