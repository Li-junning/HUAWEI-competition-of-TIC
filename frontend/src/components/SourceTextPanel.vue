<script setup lang="ts">
import { computed } from 'vue'
import type { ClaimListItem } from '../types/api'
import { segmentClaimText } from '../utils/claimHighlights'

const props = withDefaults(defineProps<{
  text: string
  claims: ClaimListItem[]
  selectedClaimId?: string | null
}>(), { selectedClaimId: null })
const emit = defineEmits<{ open: [claimId: string] }>()
const segments = computed(() => segmentClaimText(props.text, props.claims))
const invalidRanges = computed(() => props.claims.length - new Set(segments.value.flatMap(segment => segment.claimIds)).size)
</script>

<template>
  <section class="source-text-panel" aria-labelledby="source-text-title">
    <div class="source-text-heading">
      <div><p class="eyebrow">Original text</p><h2 id="source-text-title">原文与声明位置</h2></div>
      <span>{{ text.length.toLocaleString() }} 字符</span>
    </div>
    <p class="source-text-hint">点击高亮片段可打开对应声明；颜色仅用于定位，不表示结论可信度。</p>
    <div class="source-text-body"><template v-for="(segment, index) in segments" :key="index"><button
      v-if="segment.claimIds.length"
      type="button"
      class="source-claim-highlight"
      :class="{ 'is-selected': segment.claimIds.includes(selectedClaimId ?? '') }"
      :aria-label="`打开声明：${claims.find(claim => claim.claim_id === segment.claimIds[0])?.normalized_claim ?? '原文片段'}`"
      :title="claims.filter(claim => segment.claimIds.includes(claim.claim_id)).map(claim => claim.normalized_claim).join('；')"
      @click="emit('open', segment.claimIds[0])"
    >{{ segment.text }}</button><span v-else>{{ segment.text }}</span></template></div>
    <p v-if="invalidRanges" class="source-range-note">{{ invalidRanges }} 条声明的位置无效、文本不匹配或与其他声明重叠，已保守跳过对应高亮。</p>
  </section>
</template>
