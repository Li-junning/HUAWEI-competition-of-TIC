import type { ApiErrorBody, ClaimDetail, ClaimPage, ReviewEvent, TaskCreated, TaskSummary, ServiceStatus } from '../types/api'

const API_PREFIX = '/api'

export class ApiError extends Error {
  readonly code: string | null
  readonly status: number

  constructor(message: string, status: number, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), 20_000)
  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init.headers },
    })
    const raw: unknown = await response.json().catch(() => null)
    if (!response.ok) {
      const body = (raw ?? {}) as ApiErrorBody
      throw new ApiError(body.error?.message ?? `请求失败（${response.status}）`, response.status, body.error?.code ?? null)
    }
    return raw as T
  } catch (error: unknown) {
    if (error instanceof ApiError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new ApiError('请求超时，请稍后重试。', 408, 'REQUEST_TIMEOUT')
    }
    throw new ApiError('网络暂时不可用，请检查后端服务后重试。', 0, 'NETWORK_ERROR')
  } finally {
    window.clearTimeout(timeout)
  }
}

export function createTask(inputText: string): Promise<TaskCreated> {
  return requestJson<TaskCreated>('/tasks', { method: 'POST', body: JSON.stringify({ input_text: inputText, claim_limit: 15 }) })
}

export function getStatus(): Promise<ServiceStatus> { return requestJson<ServiceStatus>('/status') }

export function getTask(taskId: string): Promise<TaskSummary> {
  return requestJson<TaskSummary>(`/tasks/${encodeURIComponent(taskId)}`)
}

export function getClaims(taskId: string): Promise<ClaimPage> {
  return requestJson<ClaimPage>(`/tasks/${encodeURIComponent(taskId)}/claims?offset=0&limit=20`)
}

export function getTaskInput(taskId: string): Promise<{ task_id: string; input_text: string }> {
  return requestJson<{ task_id: string; input_text: string }>(`/tasks/${encodeURIComponent(taskId)}/input`)
}

export function getClaimDetail(claimId: string): Promise<ClaimDetail> {
  return requestJson<ClaimDetail>(`/claims/${encodeURIComponent(claimId)}`)
}

export function retryClaim(claimId: string): Promise<ClaimListItemResponse> {
  return requestJson<ClaimListItemResponse>(`/claims/${encodeURIComponent(claimId)}/retry`, { method: 'POST' })
}

export function editClaim(claimId: string, normalizedClaim: string, reviewer: string): Promise<ClaimDetail> {
  return requestJson<ClaimDetail>(`/claims/${encodeURIComponent(claimId)}`, {
    method: 'PATCH', body: JSON.stringify({ normalized_claim: normalizedClaim, reviewer }),
  })
}

export function deleteClaim(claimId: string, reviewer: string): Promise<void> {
  return requestJson<void>(`/claims/${encodeURIComponent(claimId)}?reviewer=${encodeURIComponent(reviewer)}`, { method: 'DELETE' })
}

export function addClaim(taskId: string, charStart: number, charEnd: number, normalizedClaim: string, reviewer: string): Promise<ClaimDetail> {
  return requestJson<ClaimDetail>(`/tasks/${encodeURIComponent(taskId)}/claims`, {
    method: 'POST', body: JSON.stringify({ char_start: charStart, char_end: charEnd, normalized_claim: normalizedClaim, reviewer }),
  })
}

export function splitClaim(claimId: string, splitAt: number, firstClaim: string, secondClaim: string, reviewer: string): Promise<ClaimDetail[]> {
  return requestJson<ClaimDetail[]>(`/claims/${encodeURIComponent(claimId)}/split`, {
    method: 'POST', body: JSON.stringify({ split_at: splitAt, first_claim: firstClaim, second_claim: secondClaim, reviewer }),
  })
}

export function mergeClaims(taskId: string, claimIds: string[], normalizedClaim: string, reviewer: string): Promise<ClaimDetail> {
  return requestJson<ClaimDetail>(`/tasks/${encodeURIComponent(taskId)}/claims/merge`, {
    method: 'POST', body: JSON.stringify({ claim_ids: claimIds, normalized_claim: normalizedClaim, reviewer }),
  })
}

export function getReviewHistory(taskId: string): Promise<{ items: ReviewEvent[] }> {
  return requestJson<{ items: ReviewEvent[] }>(`/tasks/${encodeURIComponent(taskId)}/review-history`)
}

export function undoReview(taskId: string, eventId: string, reviewer: string): Promise<{ undone: boolean }> {
  return requestJson<{ undone: boolean }>(`/tasks/${encodeURIComponent(taskId)}/review-history/${encodeURIComponent(eventId)}/undo`, {
    method: 'POST', body: JSON.stringify({ reviewer }),
  })
}

export function exportUrl(taskId: string, format: 'json' | 'md'): string {
  return `${API_PREFIX}/tasks/${encodeURIComponent(taskId)}/export?format=${format}`
}

interface ClaimListItemResponse {
  claim_id: string
  state: string
}
