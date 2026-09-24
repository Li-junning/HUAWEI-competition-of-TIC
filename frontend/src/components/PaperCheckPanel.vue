<script setup lang="ts">
import type { PaperCheck } from '../types/api'

const { paper } = defineProps<{ paper: PaperCheck }>()

const fields = [
  { key: 'title', label: '标题' }, { key: 'authors', label: '作者' }, { key: 'year', label: '年份' }, { key: 'venue', label: '期刊/会议' }, { key: 'doi', label: 'DOI' },
] as const

function inputValue(key: typeof fields[number]['key']): string {
  const value = paperValue(`input_${key}`)
  return Array.isArray(value) ? value.join('、') : value === null ? '未知' : String(value)
}
function candidateValue(key: typeof fields[number]['key']): string {
  const value = paperValue(`candidate_${key}`)
  return Array.isArray(value) ? value.join('、') : value === null ? '未找到' : String(value)
}
function paperValue(key: string): string | number | string[] | null {
  return (paper as unknown as Record<string, string | number | string[] | null>)[key] ?? null
}
</script>

<template>
  <section class="paper-panel">
    <div class="paper-header"><span class="eyebrow">Paper check</span><h4>论文引用核对</h4></div>
    <div class="paper-grid">
      <div v-for="field in fields" :key="field.key" class="paper-field">
        <span>{{ field.label }}</span>
        <strong :class="paper.field_matches[field.key] === true ? 'match-yes' : paper.field_matches[field.key] === false ? 'match-no' : 'match-unknown'">{{ paper.field_matches[field.key] === true ? '匹配' : paper.field_matches[field.key] === false ? '不匹配' : '未核清' }}</strong>
        <small>原文：{{ inputValue(field.key) }}</small>
        <small>候选：{{ candidateValue(field.key) }}</small>
      </div>
    </div>
    <p class="paper-result">论文存在性：<b>{{ paper.existence_status }}</b> · 是否支持原结论：<b>{{ paper.claim_support_status }}</b></p>
  </section>
</template>
