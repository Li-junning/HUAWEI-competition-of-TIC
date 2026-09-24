<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ClaimDetail, ClaimLabel, ClaimListItem, ClaimState } from '../types/api'
import EvidencePanel from './EvidencePanel.vue'
import PaperCheckPanel from './PaperCheckPanel.vue'

const props = defineProps<{ claim: ClaimListItem; detail: ClaimDetail | null; loading: boolean; retrying: boolean; retryDisabled?: boolean }>()
const emit = defineEmits<{ expand: [claimId: string]; retry: [claimId: string] }>()
const expanded = ref(false)
const queries = computed(() => [...new Set(props.detail?.queries ?? [])])
const evidenceCount = computed(() => props.detail?.evidence_clusters.reduce((sum, cluster) => sum + cluster.items.length, 0))

const labelText: Record<ClaimLabel, string> = {
  credible: '可信', disputed: '存在争议', incorrect: '错误', evidence_insufficient: '证据不足', not_applicable: '不适用',
}
function displayLabel(label: ClaimLabel | null): string { return label ? labelText[label] : '尚未判断' }
function labelClass(label: ClaimLabel | null): string { return label ? `label-${label.replace('_', '-')}` : 'label-pending' }
const typeText: Record<string, string> = {
  general: '事实声明', paper_citation: '论文引用',
  statistic: '数字统计', policy: '政策法规', paper: '论文引用', person: '人物', organization: '机构', time: '时间', location: '地点', event: '事件', technology: '技术结论', opinion: '观点',
}
const stateText: Record<ClaimState, string> = {
  pending: '等待处理', extracting: '抽取中', retrieving: '检索中', judging: '判断中',
  done: '处理完成', failed: '处理失败，尚未核验', unchecked: '尚未核验',
}

function toggle(): void {
  expanded.value = !expanded.value
  if (expanded.value && !props.detail) emit('expand', props.claim.claim_id)
}
</script>

<template>
  <article class="claim-card" :class="claim.label ? `claim-${claim.label}` : 'claim-pending'">
    <div class="claim-bar"></div>
    <div class="claim-content">
      <div class="claim-header">
        <div class="claim-heading"><span class="type-tag">{{ typeText[claim.type] ?? claim.type }}</span><span class="claim-location">原文 {{ claim.char_start }}–{{ claim.char_end }}</span></div>
        <span class="label-pill" :class="labelClass(claim.label)">{{ displayLabel(claim.label) }}</span>
      </div>
      <h3>{{ claim.normalized_claim }}</h3>
      <p class="reason">{{ claim.reason || '尚无判断说明。' }}</p>
      <div class="claim-foot">
        <span>状态：{{ stateText[claim.state] }}</span>
        <span v-if="claim.support_score !== null">支持指数 {{ claim.support_score }}</span>
        <span v-if="claim.retry_count">已重试 {{ claim.retry_count }} 次</span>
        <button type="button" class="text-button" :aria-expanded="expanded" :aria-controls="`detail-${claim.claim_id}`" @click="toggle">{{ expanded ? '收起详情' : '查看证据与细节' }} <span aria-hidden="true">{{ expanded ? '↑' : '↓' }}</span></button>
      </div>
      <div v-if="expanded" :id="`detail-${claim.claim_id}`" class="claim-detail">
        <div v-if="loading || retrying" class="loading-line">{{ retrying ? '正在重新检索，完成后更新证据…' : '正在读取证据…' }}</div>
        <template v-else-if="detail">
          <div class="evidence-heading">核查证据 <span>{{ evidenceCount }} 条</span></div>
          <PaperCheckPanel v-if="detail.paper_check" :paper="detail.paper_check" />
          <EvidencePanel :clusters="detail.evidence_clusters" />
          <details class="retrieval-details">
            <summary>原文与检索过程<span v-if="queries.length"> · {{ queries.length }} 组检索词</span></summary>
            <blockquote>{{ claim.source_text }}</blockquote>
            <div v-if="detail.conditions.length" class="detail-row"><b>限定条件：</b>{{ detail.conditions.join('；') }}</div>
            <div v-if="detail.entities.length" class="detail-row"><b>涉及实体：</b>{{ detail.entities.join('、') }}</div>
            <ol v-if="queries.length" class="query-list"><li v-for="query in queries" :key="query">{{ query }}</li></ol>
            <p v-else class="detail-row">未记录检索词。</p>
          </details>
          <div v-if="claim.label === 'evidence_insufficient' || claim.state === 'failed'" class="retry-row">
            <button type="button" class="secondary-button" :disabled="retrying || retryDisabled || claim.retry_count >= 2" @click="emit('retry', claim.claim_id)">{{ retrying ? '提交中…' : retryDisabled ? '任务处理中' : claim.retry_count >= 2 ? '已达重试上限' : '重新检索' }}</button>
            <span>剩余 {{ Math.max(0, 2 - claim.retry_count) }} 次重试</span>
          </div>
        </template>
      </div>
    </div>
  </article>
</template>
