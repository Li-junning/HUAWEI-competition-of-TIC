<script setup lang="ts">
import type { EvidenceCluster, EvidenceItem } from '../types/api'
import { formatDate, safeExternalUrl } from '../utils/security'

defineProps<{ clusters: EvidenceCluster[] }>()

function relationLabel(relation: EvidenceItem['relation']): string {
  const labels: Record<EvidenceItem['relation'], string> = {
    supports: '支持', refutes: '反驳', partially_supports: '部分支持', irrelevant: '不相关', unknown: '未知',
  }
  return labels[relation]
}
</script>

<template>
  <div v-if="clusters.length" class="evidence-list">
    <div v-for="cluster in clusters" :key="cluster.cluster_id" class="evidence-cluster">
      <p class="cluster-note">证据簇 · {{ cluster.independence_reason }}</p>
      <article v-for="item in cluster.items" :key="item.evidence_id" class="evidence-item">
        <div class="evidence-topline">
          <span class="relation" :class="`relation-${item.relation}`">{{ relationLabel(item.relation) }}</span>
          <span v-if="item.is_reprint" class="reprint-tag">转载</span>
          <a v-if="safeExternalUrl(item.url)" :href="safeExternalUrl(item.url) ?? undefined" target="_blank" rel="noopener noreferrer" class="source-link">打开来源 ↗</a>
        </div>
        <h4>{{ item.title || '无标题来源' }}</h4>
        <p class="source-meta">{{ item.publisher || '未知发布方' }} · 发布 {{ formatDate(item.published_at) }} · 取证 {{ formatDate(item.retrieved_at) }}</p>
        <p class="excerpt">{{ item.excerpt || '未返回可核查正文片段。' }}</p>
        <p class="quality-reason">来源说明：{{ item.quality_reason || '未提供' }}</p>
        <p class="raw-url">{{ safeExternalUrl(item.url) ?? '来源地址未通过 HTTP(S) 校验' }}</p>
      </article>
    </div>
  </div>
  <div v-else class="evidence-empty"><strong>本次暂无可展示的证据</strong><p>可展开检索过程排查，或重新检索。缺少证据不代表声明正确，也不能作为判错依据。</p></div>
</template>
