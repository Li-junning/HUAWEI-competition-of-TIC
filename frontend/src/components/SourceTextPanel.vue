<script setup lang="ts">
import { computed } from 'vue'
import type { ClaimLabel, ClaimListItem } from '../types/api'
import { segmentClaimText } from '../utils/claimHighlights'

const props = withDefaults(defineProps<{
  text: string
  claims: ClaimListItem[]
  selectedClaimId?: string | null
}>(), { selectedClaimId: null })
const emit = defineEmits<{ open: [claimId: string] }>()
const segments = computed(() => segmentClaimText(props.text, props.claims))
const invalidRanges = computed(() => props.claims.length - new Set(segments.value.flatMap(segment => segment.claimIds)).size)
const claimsById = computed(() => new Map(props.claims.map(claim => [claim.claim_id, claim])))
const labelText: Record<ClaimLabel, string> = {
  credible: '可信', disputed: '存在争议', incorrect: '错误', evidence_insufficient: '证据不足', not_applicable: '不适用',
}
function resultLabel(claimId: string): string {
  const label = claimsById.value.get(claimId)?.label
  return label ? labelText[label] : '尚未判断'
}
</script>

<template>
  <section class="source-text-panel" aria-labelledby="source-text-title">
    <div class="source-text-heading">
      <div><p class="eyebrow">Original text</p><h2 id="source-text-title">原文与声明位置</h2></div>
      <span>{{ text.length.toLocaleString() }} 字符</span>
    </div>
    <p class="source-text-hint">底色对应声明结果标签，点击高亮片段可查看详情；颜色不代表正确概率。</p>
    <div class="source-text-body"><template v-for="(segment, index) in segments" :key="index"><button
      v-if="segment.claimIds.length"
      type="button"
      class="source-claim-highlight"
      :class="[`source-claim-${claimsById.get(segment.claimIds[0])?.label ?? 'pending'}`, { 'is-selected': segment.claimIds.includes(selectedClaimId ?? '') }]"
      :aria-label="`打开${resultLabel(segment.claimIds[0])}声明：${claimsById.get(segment.claimIds[0])?.normalized_claim ?? '原文片段'}`"
      :title="`${resultLabel(segment.claimIds[0])}：${claimsById.get(segment.claimIds[0])?.normalized_claim ?? '原文片段'}`"
      @click="emit('open', segment.claimIds[0])"
    >{{ segment.text }}</button><span v-else>{{ segment.text }}</span></template></div>
    <p v-if="invalidRanges" class="source-range-note">{{ invalidRanges }} 条声明的位置无效、文本不匹配或与其他声明重叠，已保守跳过对应高亮。</p>
  </section>
</template>
