<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ClaimListItem, ReviewEvent } from '../types/api'

const props = defineProps<{
  text: string
  claims: ClaimListItem[]
  history: ReviewEvent[]
  reviewer: string
  busy: boolean
  disabled: boolean
  limit: number
}>()
const emit = defineEmits<{
  'update:reviewer': [value: string]
  add: [start: number, end: number, wording: string]
  split: [claimId: string, offset: number, first: string, second: string]
  merge: [ids: string[], wording: string]
  undo: [eventId: string]
}>()
const mode = ref<'add' | 'split' | 'merge'>('add')
const sourceField = ref<HTMLTextAreaElement | null>(null)
const splitField = ref<HTMLTextAreaElement | null>(null)
const addStart = ref(0)
const addEnd = ref(0)
const addWording = ref('')
const splitId = ref('')
const splitPosition = ref(0)
const splitFirst = ref('')
const splitSecond = ref('')
const mergeId = ref('')
const mergeWording = ref('')
const ordered = computed(() => [...props.claims].sort((a, b) => a.char_start - b.char_start))
const selectedSplit = computed(() => ordered.value.find(claim => claim.claim_id === splitId.value))
const mergeIndex = computed(() => ordered.value.findIndex(claim => claim.claim_id === mergeId.value))
const mergeNext = computed(() => mergeIndex.value < 0 ? undefined : ordered.value[mergeIndex.value + 1])
const latestUndoable = computed(() => props.history.find(event => !event.undone_at)?.event_id)
const selection = computed(() => props.text.slice(addStart.value, addEnd.value))
const actionLabels: Record<ReviewEvent['action'], string> = {
  add: '补充声明', split: '拆分声明', merge: '合并声明', edit: '修改表述', delete: '删除声明',
}

function captureAddSelection(): void {
  if (!sourceField.value) return
  addStart.value = sourceField.value.selectionStart
  addEnd.value = sourceField.value.selectionEnd
  addWording.value = selection.value.trim()
}

function captureSplitPosition(): void {
  if (!selectedSplit.value || !splitField.value) return
  splitPosition.value = selectedSplit.value.char_start + splitField.value.selectionStart
  splitFirst.value = props.text.slice(selectedSplit.value.char_start, splitPosition.value).trim()
  splitSecond.value = props.text.slice(splitPosition.value, selectedSplit.value.char_end).trim()
}

watch(splitId, () => {
  splitPosition.value = 0
  splitFirst.value = ''
  splitSecond.value = ''
})
watch(mergeId, () => {
  const first = ordered.value[mergeIndex.value]
  mergeWording.value = first && mergeNext.value
    ? props.text.slice(first.char_start, mergeNext.value.char_end).trim() : ''
})
</script>

<template>
  <section class="review-workbench" aria-labelledby="review-workbench-title">
    <div class="review-workbench-header">
      <div><p class="eyebrow">Human review</p><h2 id="review-workbench-title">人工调整断句</h2><p>调整后需重新检索，旧判断不会沿用。</p></div>
      <label class="reviewer-field">审核人署名<input :value="reviewer" maxlength="40" placeholder="请输入姓名或代号" :disabled="busy" @input="emit('update:reviewer', ($event.target as HTMLInputElement).value)" /><small>署名用于操作记录，未验证身份</small></label>
    </div>
    <div class="review-mode-tabs" role="group" aria-label="选择人工调整方式">
      <button v-for="item in (['add', 'split', 'merge'] as const)" :key="item" type="button" :class="{ active: mode === item }" @click="mode = item">{{ { add: '补充遗漏声明', split: '拆分声明', merge: '合并相邻声明' }[item] }}</button>
    </div>
    <div v-if="mode === 'add'" class="review-form">
      <p>在下方原文中选中遗漏的完整片段，系统会自动记录位置。</p>
      <textarea ref="sourceField" class="review-source-field" :value="text" readonly rows="5" aria-label="选中要补充的原文片段" @select="captureAddSelection" @mouseup="captureAddSelection" @keyup="captureAddSelection"></textarea>
      <small>选区 {{ addStart }}–{{ addEnd }}：{{ selection || '尚未选择' }}</small>
      <label>声明文字<input v-model="addWording" maxlength="2000" placeholder="填写需要核验的事实声明" /></label>
      <button type="button" class="primary-button small" :disabled="busy || disabled || claims.length >= limit || addEnd <= addStart || !addWording.trim()" @click="emit('add', addStart, addEnd, addWording.trim())">补充声明</button>
      <small v-if="claims.length >= limit">当前已达到 {{ limit }} 条声明上限；可先合并或删除。</small>
    </div>
    <div v-else-if="mode === 'split'" class="review-form">
      <label>选择要拆分的声明<select v-model="splitId"><option value="">请选择</option><option v-for="claim in ordered" :key="claim.claim_id" :value="claim.claim_id">{{ claim.char_start }}–{{ claim.char_end }} · {{ claim.normalized_claim }}</option></select></label>
      <template v-if="selectedSplit">
        <p>在下方原文中点击两条声明的分界位置，再检查拆分后的文字。</p>
        <textarea ref="splitField" class="review-source-field" :value="selectedSplit.source_text" readonly rows="3" aria-label="点击断句位置" @click="captureSplitPosition" @keyup="captureSplitPosition" @select="captureSplitPosition"></textarea>
        <small>分界位置：{{ splitPosition || '尚未选择' }}</small>
        <label>第一条声明<input v-model="splitFirst" maxlength="2000" /></label>
        <label>第二条声明<input v-model="splitSecond" maxlength="2000" /></label>
        <button type="button" class="primary-button small" :disabled="busy || disabled || claims.length >= limit || splitPosition <= selectedSplit.char_start || splitPosition >= selectedSplit.char_end || !splitFirst.trim() || !splitSecond.trim()" @click="emit('split', selectedSplit.claim_id, splitPosition, splitFirst.trim(), splitSecond.trim())">确认拆分</button>
      </template>
    </div>
    <div v-else class="review-form">
      <label>选择第一条声明<select v-model="mergeId"><option value="">请选择</option><option v-for="claim in ordered.slice(0, -1)" :key="claim.claim_id" :value="claim.claim_id">{{ claim.char_start }}–{{ claim.char_end }} · {{ claim.normalized_claim }}</option></select></label>
      <p v-if="mergeNext">将与下一条合并：{{ mergeNext.normalized_claim }}</p>
      <label v-if="mergeNext">合并后的声明<input v-model="mergeWording" maxlength="2000" /></label>
      <button type="button" class="primary-button small" :disabled="busy || disabled || !mergeId || !mergeNext || !mergeWording.trim()" @click="emit('merge', [mergeId, mergeNext!.claim_id], mergeWording.trim())">确认合并</button>
    </div>
    <details class="review-history">
      <summary>操作记录（{{ history.length }}）</summary>
      <p v-if="!history.length">暂无人工操作。</p>
      <ol v-else>
        <li v-for="event in history" :key="event.event_id">
          <div><strong>{{ actionLabels[event.action] }}</strong><span>{{ event.reviewer }} · {{ new Date(event.created_at).toLocaleString('zh-CN') }}</span><span v-if="event.undone_at">已由 {{ event.undone_by }} 撤销</span></div>
          <p v-if="event.before_text">修改前：{{ event.before_text }}</p><p v-if="event.after_text">修改后：{{ event.after_text }}</p>
          <button v-if="event.event_id === latestUndoable" type="button" class="secondary-button" :disabled="busy || disabled" @click="emit('undo', event.event_id)">撤销这次操作</button>
        </li>
      </ol>
    </details>
  </section>
</template>
