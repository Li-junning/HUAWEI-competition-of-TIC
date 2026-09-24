import { computed, ref } from 'vue'
import type { Ref } from 'vue'
import type { ClaimLabel, ClaimListItem } from '../types/api'

function riskRank(label: ClaimLabel | null): number {
  return label === null ? 2 : { incorrect: 5, disputed: 4, evidence_insufficient: 3, credible: 1, not_applicable: 0 }[label]
}

export function useClaimFilters(claims: Ref<ClaimListItem[]>) {
  const filter = ref<'all' | ClaimLabel>('all')
  const sortRisk = ref(false)
  const filteredClaims = computed(() => {
    const result = filter.value === 'all' ? [...claims.value] : claims.value.filter((claim) => claim.label === filter.value)
    if (sortRisk.value) result.sort((a, b) => riskRank(b.label) - riskRank(a.label))
    return result
  })
  return { filter, sortRisk, filteredClaims }
}
