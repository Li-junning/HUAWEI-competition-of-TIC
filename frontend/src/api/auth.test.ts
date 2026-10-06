import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTask, getAuthSession, loginWorkspace, logoutWorkspace, setCsrfToken } from './client'

afterEach(() => { setCsrfToken(null); vi.unstubAllGlobals() })
describe('workspace session security', () => {
  it('sends cookies and a CSRF token on writes, keeps the token out of reads', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    setCsrfToken('synthetic-csrf')
    await createTask('测试')
    const write = fetchMock.mock.calls[0]![1]!
    expect(write.credentials).toBe('same-origin')
    expect(new Headers(write.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    await getAuthSession()
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).has('X-CSRF-Token')).toBe(false)
  })
  it('requires the custom login header and sends logout with CSRF protection', async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response('{}'))
    vi.stubGlobal('fetch', fetchMock)
    await loginWorkspace('synthetic-password')
    expect(new Headers(fetchMock.mock.calls[0]![1]?.headers).get('X-Verifier-Request')).toBe('1')
    setCsrfToken('synthetic-csrf')
    await logoutWorkspace()
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
  })
  it('locks the UI and clears the token when the server expires the session', async () => {
    const dispatch = vi.fn()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response('{"error":{"code":"AUTH_REQUIRED","message":"登录失效"}}', { status: 401 }))
      .mockResolvedValue(new Response('{}'))
    vi.stubGlobal('fetch', fetchMock); vi.stubGlobal('dispatchEvent', dispatch)
    setCsrfToken('expired-token')
    await expect(createTask('测试')).rejects.toMatchObject({ status: 401, code: 'AUTH_REQUIRED' })
    expect(dispatch).toHaveBeenCalledOnce()
    await createTask('测试')
    expect(new Headers(fetchMock.mock.calls[1]![1]?.headers).has('X-CSRF-Token')).toBe(false)
  })
})
