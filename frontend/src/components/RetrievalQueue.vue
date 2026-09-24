<script setup lang="ts">
import { computed } from 'vue'
import type { ClaimListItem, ClaimState } from '../types/api'

const props = defineProps<{ claims: ClaimListItem[] }>()

const activeStates: ClaimState[] = ['extracting', 'retrieving', 'judging']
const stateText: Record<ClaimState, string> = {
  pending: '等待处理',
  extracting: '抽取中',
  retrieving: '检索中',
  judging: '判断中',
  done: '已完成',
  failed: '处理失败',
  unchecked: '未核验',
}

const visibleClaims = computed(() => {
  const completed = props.claims.filter(claim => claim.state === 'done').slice(-1)
  const active = props.claims.filter(claim => activeStates.includes(claim.state))
  const remaining = props.claims.filter(claim => claim.state !== 'done' && !activeStates.includes(claim.state))
  const extraCompleted = props.claims.filter(claim => claim.state === 'done').slice(0, -1).reverse()
  return [...completed, ...active, ...remaining, ...extraCompleted].slice(0, 4)
})
const hiddenCount = computed(() => props.claims.length - visibleClaims.value.length)
</script>

<template>
  <section class="retrieval-queue" aria-label="声明处理队列">
    <div class="retrieval-queue-head">
      <strong>处理队列</strong>
      <span>{{ claims.length ? `已返回 ${claims.length} 条声明` : '正在识别可核验声明' }}</span>
    </div>
    <div v-if="claims.length" class="retrieval-queue-list">
      <div
        v-for="claim in visibleClaims"
        :key="claim.claim_id"
        class="retrieval-queue-row"
        :class="`queue-${claim.state}`"
      >
        <span class="retrieval-queue-dot" aria-hidden="true"></span>
        <span class="retrieval-queue-text" :title="claim.normalized_claim">{{ claim.normalized_claim }}</span>
        <span class="retrieval-queue-state">{{ stateText[claim.state] }}</span>
      </div>
      <p v-if="hiddenCount" class="retrieval-queue-more">其余 {{ hiddenCount }} 条声明可在下方查看</p>
    </div>
    <div v-else class="retrieval-queue-placeholder" aria-hidden="true">
      <span class="retrieval-queue-placeholder-dot"></span>
      <span class="retrieval-queue-placeholder-line"></span>
      <span class="retrieval-queue-placeholder-line short"></span>
    </div>
  </section>
</template>
