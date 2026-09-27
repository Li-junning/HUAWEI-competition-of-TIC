<script setup lang="ts">
import { computed } from 'vue'
import type { ClaimListItem, ReviewEvent } from '../types/api'
import ReviewWorkbench from './ReviewWorkbench.vue'

const props = defineProps<{
  taskId: string
  text: string
  sourceLoading: boolean
  sourceError: string | null
  claims: ClaimListItem[]
  history: ReviewEvent[]
  reviewer: string
  busy: boolean
  disabled: boolean
  limit: number
  errorMessage: string | null
}>()
const emit = defineEmits<{
  back: []
  reload: []
  'update:reviewer': [value: string]
  add: [start: number, end: number, wording: string]
  split: [claimId: string, offset: number, first: string, second: string]
  merge: [ids: string[], wording: string]
  undo: [eventId: string]
}>()
const ordered = computed(() => [...props.claims].sort((a, b) => a.char_start - b.char_start))
function forwardAdd(start: number, end: number, wording: string): void { emit('add', start, end, wording) }
function forwardSplit(claimId: string, offset: number, first: string, second: string): void { emit('split', claimId, offset, first, second) }
function forwardMerge(ids: string[], wording: string): void { emit('merge', ids, wording) }
</script>

<template>
  <section class="review-page" aria-labelledby="review-page-title">
    <div class="review-page-top"><button type="button" class="secondary-button" @click="emit('back')">← 返回核验结果</button><span>任务 {{ taskId }}</span></div>
    <div class="review-page-intro"><p class="eyebrow">Human review workspace</p><h1 id="review-page-title">人工调整断句</h1><p>在原文中补充、拆分或合并声明。调整过的声明会变为未核验，可回到报告重新检索。</p></div>
    <p v-if="errorMessage" class="global-error" role="alert">{{ errorMessage }}</p>
    <p v-if="disabled" class="notice warning">任务仍在处理中，完成后才可调整断句。</p>
    <div class="review-page-layout">
      <div class="review-page-main">
        <ReviewWorkbench v-if="text" :text="text" :claims="claims" :history="history" :reviewer="reviewer" :busy="busy" :disabled="disabled" :limit="limit" @update:reviewer="emit('update:reviewer', $event)" @add="forwardAdd" @split="forwardSplit" @merge="forwardMerge" @undo="emit('undo', $event)" />
        <div v-else-if="sourceLoading" class="loading-panel">正在读取原文…</div>
        <div v-else class="notice warning">原文暂不可用：{{ sourceError || '请稍后重试' }} <button type="button" class="text-button" @click="emit('reload')">重新加载原文</button></div>
      </div>
      <aside class="review-page-aside" aria-label="当前断句概览">
        <div class="review-page-aside-heading"><p class="eyebrow">Current claims</p><h2>当前断句 <span>{{ claims.length }} / {{ limit }}</span></h2></div>
        <ol v-if="ordered.length" class="review-page-claims"><li v-for="(claim, index) in ordered" :key="claim.claim_id"><span>{{ String(index + 1).padStart(2, '0') }}</span><div><small>原文 {{ claim.char_start }}–{{ claim.char_end }} · {{ claim.manually_edited ? '人工调整' : '自动提取' }}</small><p>{{ claim.normalized_claim }}</p></div></li></ol>
        <p v-else class="review-page-empty">当前没有声明，可从原文中选区补充。</p>
      </aside>
    </div>
  </section>
</template>
