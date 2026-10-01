import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createTask, deleteClaim, getTask, splitClaim, getKnowledgeStatus, getKnowledgeDocument, importKnowledgeDocument } from './client'

describe('API request reliability', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('reads a successful response and clears its deadline', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ task_id: 't_test', status: 'created' }), { status: 202 }))
    await expect(createTask('待核验文本')).resolves.toEqual({ task_id: 't_test', status: 'created' })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('preserves the server error message, code and HTTP status', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { message: '操作太频繁', code: 'TASK_RATE_LIMIT' } }), { status: 429 }))
    await expect(createTask('待核验文本')).rejects.toMatchObject({
      name: 'ApiError', message: '操作太频繁', code: 'TASK_RATE_LIMIT', status: 429,
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('explains that a generic knowledge 404 needs a backend restart', async () => {
    fetchMock.mockResolvedValue(new Response('{"detail":"Not Found"}', { status: 404 }))
    await expect(getKnowledgeStatus()).rejects.toMatchObject({
      status: 404, code: 'KNOWLEDGE_API_UNAVAILABLE', message: expect.stringContaining('重新启动'),
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not confuse a deleted source with an old backend', async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ error: { code: 'KB_NOT_FOUND', message: '资料不存在或已删除。' } }), { status: 404 }))
    await expect(getKnowledgeDocument('kd_deleted')).rejects.toMatchObject({
      status: 404, code: 'KB_NOT_FOUND', message: '资料不存在或已删除。',
    })
  })

  it('does not repeat an import against an old backend or call it a timeout', async () => {
    fetchMock.mockResolvedValue(new Response('{"detail":"Not Found"}', { status: 404 }))
    const pending = importKnowledgeDocument({ title: '资料', publisher: null, source_url: null, published_at: null, tags: [], content: '资料正文' })
    await expect(pending).rejects.toMatchObject({ code: 'KNOWLEDGE_API_UNAVAILABLE' })
    await expect(pending).rejects.not.toMatchObject({ message: expect.stringContaining('超时') })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each([
    '<html>upstream unavailable</html>', 'null', '[]', '{"error":null}',
    '{"error":"unavailable"}', '{"error":{"message":{"value":"bad"},"code":123}}',
    '{"error":{"message":" ","code":" "}}',
  ])('keeps the HTTP status when the error body is malformed: %s', async body => {
    fetchMock.mockResolvedValue(new Response(body, { status: 503 }))
    await expect(getTask('t_test')).rejects.toMatchObject({
      name: 'ApiError', message: '请求失败（503）', status: 503, code: null,
    })
  })

  it.each(['<html>proxy page</html>', '', '{"task_id":', 'null', 'true', '42', '"ok"'])
    ('rejects a malformed success response without repeating a write: %s', async body => {
      fetchMock.mockResolvedValue(new Response(body, { status: 202 }))
      await expect(createTask('待核验文本')).rejects.toMatchObject({
        name: 'ApiError', message: expect.stringContaining('数据格式异常'), status: 202, code: 'INVALID_RESPONSE',
      })
      expect(fetchMock).toHaveBeenCalledTimes(1)
      expect(vi.getTimerCount()).toBe(0)
    })

  it('allows array responses used by splitting claims', async () => {
    fetchMock.mockResolvedValue(new Response('[]'))
    await expect(splitClaim('c_test', 2, '前半句', '后半句', '审核人')).resolves.toEqual([])
  })

  it('accepts an empty 204 deletion response', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(deleteClaim('c_test', '审核人')).resolves.toBeUndefined()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects an empty 204 response from an endpoint that requires JSON', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(getTask('t_test')).rejects.toMatchObject({ status: 204, code: 'INVALID_RESPONSE' })
  })

  it('wraps network failures without repeating the request', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(createTask('待核验文本')).rejects.toMatchObject({
      name: 'ApiError', status: 0, code: 'NETWORK_ERROR',
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it.each(['headers', 'json', 'error-json'] as const)('times out while waiting for %s', async phase => {
    fetchMock.mockImplementation((_url, init) => {
      const pending = () => new Promise<never>((_resolve, reject) => {
        init!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
      })
      if (phase === 'headers') return pending()
      return Promise.resolve({ ok: phase !== 'error-json', status: phase === 'error-json' ? 503 : 200, json: pending } as unknown as Response)
    })
    const rejection = expect(getTask('t_test')).rejects.toMatchObject({
      name: 'ApiError', status: 408, code: 'REQUEST_TIMEOUT',
    })
    await vi.advanceTimersByTimeAsync(20_000)
    await rejection
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not classify a broken response body as valid JSON', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 200, json: () => Promise.reject(new TypeError('body stream failed')) } as unknown as Response)
    const pending = getTask('t_test')
    await expect(pending).rejects.toBeInstanceOf(ApiError)
    await expect(pending).rejects.toMatchObject({ status: 0, code: 'NETWORK_ERROR' })
  })
})
