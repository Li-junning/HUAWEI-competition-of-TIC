<script setup lang="ts">
import type { TaskStatus, TaskSummary } from '../types/api'
import { percent } from '../utils/security'

defineProps<{ task: TaskSummary }>()
const statusText: Record<TaskStatus, string> = {
  created: '等待处理', running: '核验中', succeeded: '处理完成',
  partial: '部分完成', failed: '处理失败', interrupted: '已中断',
}
</script>

<template>
  <section class="coverage-panel" aria-labelledby="coverage-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">Coverage snapshot</p>
        <h2 id="coverage-title">证据覆盖摘要</h2>
      </div>
      <span class="status-pill" :class="`status-${task.status}`">{{ statusText[task.status] }}</span>
    </div>
    <p class="notice info" v-if="task.segmentation_method">语句切分：{{ task.segmentation_method === 'mimo' ? '模型语义切分' : task.segmentation_method === 'rules_fallback' ? '模型切分未通过，已回退本地规则' : '本地规则' }}</p>
    <div class="metric-grid">
      <div class="metric"><span>已抽取声明</span><strong>{{ task.claims_extracted }}</strong><small>上限 {{ task.claim_limit }}</small></div>
      <div class="metric"><span>已处理 / 未核验</span><strong>{{ task.claims_processed }} <i>/ {{ task.claims_unchecked }}</i></strong><small>含部分完成</small></div>
      <div class="metric"><span>处理覆盖率</span><strong>{{ percent(task.coverage.processing_coverage) }}</strong><small>可核验声明范围</small></div>
      <div class="metric"><span>核验覆盖率</span><strong>{{ percent(task.coverage.verification_coverage) }}</strong><small>有充分证据的声明</small></div>
      <div class="metric score-metric"><span>已判定事实平均支持指数</span><strong>{{ task.score === null ? '暂不可评估' : task.score }}</strong><small>{{ task.score_note ?? '覆盖足够且无技术失败时显示' }}</small></div>
    </div>
    <div class="label-counts" aria-label="标签统计">
      <span class="label-credible">可信 {{ task.label_counts.credible }}</span>
      <span class="label-disputed">争议 {{ task.label_counts.disputed }}</span>
      <span class="label-incorrect">错误 {{ task.label_counts.incorrect }}</span>
      <span class="label-insufficient">证据不足 {{ task.label_counts.evidence_insufficient }}</span>
      <span class="label-na">不适用 {{ task.label_counts.not_applicable }}</span>
    </div>
    <div v-if="task.truncated || task.failed_providers.length || task.score === null" class="notice-stack">
      <p v-if="task.truncated" class="notice warning">本次最多处理 {{ task.claim_limit }} 条声明，仍有 {{ task.claims_unchecked }} 条未核验。</p>
      <p v-if="task.failed_providers.length" class="notice warning">部分来源失败：{{ task.failed_providers.join('、') }}。失败不能作为反证。</p>
      <p v-if="task.score === null" class="notice info">总体支持指数暂不显示；它不是事实为真的概率，也不能代表全文正确率。</p>
    </div>
  </section>
</template>
