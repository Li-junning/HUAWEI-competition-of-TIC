import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { downloadReport } from './export'

describe('report download reliability', () => {
  const fetchMock = vi.fn<typeof fetch>()
  const anchor = { href: '', download: '', click: vi.fn() }

  beforeEach(() => {
    fetchMock.mockReset()
    anchor.href = ''
    anchor.download = ''
    anchor.click.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('document', { createElement: vi.fn(() => anchor) })
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:report')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it.each(['json', 'md'] as const)('downloads the complete %s report and releases the blob', async format => {
    const content = format === 'json' ? '{"task_id":"t_test"}' : '# 核验报告'
    fetchMock.mockResolvedValue(new Response(content))
    await downloadReport('t_test', format)
    expect(fetchMock).toHaveBeenCalledWith(`/api/tasks/t_test/export?format=${format}`, expect.objectContaining({
      credentials: 'same-origin', signal: expect.any(AbortSignal),
    }))
    expect(new Headers(fetchMock.mock.calls[0]![1]?.headers).get('Accept')).toBe(format === 'json' ? 'application/json' : 'text/markdown')
    expect(anchor.href).toBe('blob:report')
    expect(anchor.download).toBe(`verification-t_test.${format}`)
    expect(anchor.click).toHaveBeenCalledTimes(1)
    const blob = vi.mocked(URL.createObjectURL).mock.calls[0]![0] as Blob
    expect(await blob.text()).toBe(content)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report')
    expect(vi.getTimerCount()).toBe(0)
  })

  it('uses the same HTTP error contract as other API requests', async () => {
    fetchMock.mockResolvedValue(new Response('{"error":{"message":"任务未完成","code":"TASK_BUSY"}}', { status: 409 }))
    await expect(downloadReport('t_test', 'md')).rejects.toMatchObject({
      name: 'ApiError', status: 409, code: 'TASK_BUSY', message: '任务未完成',
    })
    expect(anchor.click).not.toHaveBeenCalled()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('keeps the HTTP status if the export error response has invalid fields', async () => {
    fetchMock.mockResolvedValue(new Response('{"error":{"message":false,"code":{}}}', { status: 502 }))
    await expect(downloadReport('t_test', 'json')).rejects.toMatchObject({
      name: 'ApiError', status: 502, code: null, message: '请求失败（502）',
    })
    expect(URL.createObjectURL).not.toHaveBeenCalled()
  })

  it('shows a useful API error on network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(downloadReport('t_test', 'json')).rejects.toMatchObject({
      name: 'ApiError', status: 0, code: 'NETWORK_ERROR', message: expect.stringContaining('网络暂时不可用'),
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it.each(['headers', 'body'] as const)('times out a download stalled at %s', async phase => {
    fetchMock.mockImplementation((_url, init) => {
      const pending = () => new Promise<never>((_resolve, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
      if (phase === 'headers') return pending()
      return Promise.resolve({ ok: true, status: 200, blob: pending } as unknown as Response)
    })
    const rejection = expect(downloadReport('t_test', 'md')).rejects.toMatchObject({
      name: 'ApiError', status: 408, code: 'REQUEST_TIMEOUT',
    })
    await vi.advanceTimersByTimeAsync(20_000)
    await rejection
    expect(anchor.click).not.toHaveBeenCalled()
    expect(URL.createObjectURL).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('releases the blob even if initiating the download fails', async () => {
    fetchMock.mockResolvedValue(new Response('# 核验报告'))
    anchor.click.mockImplementation(() => { throw new Error('download unavailable') })
    await expect(downloadReport('t_test', 'md')).rejects.toThrow('download unavailable')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:report')
  })
})
