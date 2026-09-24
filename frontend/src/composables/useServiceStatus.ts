import { ref } from 'vue'
import { getStatus } from '../api/client'
import type { ServiceStatus } from '../types/api'

export function useServiceStatus() {
  const serviceStatus = ref<ServiceStatus | null>(null)
  getStatus().then((status) => {
    serviceStatus.value = status
  }).catch(() => {
    serviceStatus.value = {
      search_mode: 'unknown', judge_mode: 'unknown', ready: false, live: false,
      message: '无法确认核验服务状态，请检查后端',
    }
  })
  return serviceStatus
}
