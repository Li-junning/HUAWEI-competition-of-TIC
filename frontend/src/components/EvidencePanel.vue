<script setup lang="ts">
import type { EvidenceCluster, EvidenceItem } from '../types/api'
import { formatDate, safeExternalUrl } from '../utils/security'

defineProps<{ clusters: EvidenceCluster[] }>()
const groups = (cluster: EvidenceCluster) => ([
  { key: 'supports', title: '支持', items: cluster.items.filter(item => item.relation === 'supports') },
  { key: 'partial', title: '部分支持', items: cluster.items.filter(item => item.relation === 'partially_supports') },
  { key: 'refutes', title: '反驳', items: cluster.items.filter(item => item.relation === 'refutes') },
  { key: 'pending', title: '待确认或不相关', items: cluster.items.filter(item => item.relation === 'unknown' || item.relation === 'irrelevant') },
].filter(group => group.items.length > 0))
function relationLabel(relation: EvidenceItem['relation']): string {
  const labels: Record<EvidenceItem['relation'], string> = { supports: '支持', refutes: '反驳', partially_supports: '部分支持', irrelevant: '不相关', unknown: '待确认' }
  return labels[relation]
}
function knowledgeUrl(item: EvidenceItem): string {
  const url = new URL(window.location.href)
  url.searchParams.set('library', '1')
  url.searchParams.set('kb_document', item.knowledge_document_id ?? '')
  url.searchParams.set('kb_page', String(item.source_page ?? 1))
  url.searchParams.set('kb_start', String(item.source_char_start ?? 0))
  url.searchParams.set('kb_end', String(item.source_char_end ?? 0))
  return `${url.pathname}${url.search}${url.hash}`
}
</script>

<template>
  <div v-if="clusters.length" class="evidence-list">
    <div v-for="cluster in clusters" :key="cluster.cluster_id" class="evidence-cluster">
      <p class="cluster-note">证据簇 · {{ cluster.independence_reason }}</p>
      <section v-for="group in groups(cluster)" :key="group.key" class="evidence-relation-group" :aria-label="group.title">
        <h3 class="evidence-group-title">{{ group.title }} <span>{{ group.items.length }}</span></h3>
        <article v-for="item in group.items" :key="item.evidence_id" class="evidence-item">
          <div class="evidence-topline">
            <span class="relation" :class="'relation-' + item.relation">{{ relationLabel(item.relation) }}</span>
            <span v-if="item.is_reprint" class="reprint-tag">转载</span>
            <a v-if="item.source_type === 'knowledge' && item.knowledge_document_id" :href="knowledgeUrl(item)" class="source-link">定位知识库原文 →</a>
            <a v-if="safeExternalUrl(item.url)" :href="safeExternalUrl(item.url) ?? undefined" target="_blank" rel="noopener noreferrer" class="source-link">{{ item.source_type === 'knowledge' ? '打开填写的出处 ↗' : '打开来源 ↗' }}</a>
          </div>
          <h4>{{ item.title || '无标题来源' }}</h4>
          <p class="source-meta">{{ item.publisher || '未知发布方' }} · 发布 {{ formatDate(item.published_at) }} · 取证 {{ formatDate(item.retrieved_at) }}</p>
          <p v-if="item.source_type === 'knowledge'" class="source-meta">我的知识库 · {{ item.source_page ? `第 ${item.source_page} 页 · ` : '' }}正文字符 {{ item.source_char_start }}–{{ item.source_char_end }} · 版本 {{ item.content_hash?.slice(0, 12) }}</p>
          <blockquote class="excerpt">{{ item.excerpt || '未返回可核查正文片段。' }}</blockquote>
          <details v-if="item.checks?.length" class="evidence-checks">
            <summary>逐项核对（{{ item.checks.length }} 项）</summary>
            <div v-for="check in item.checks" :key="check.part_id">
              <p><span class="relation" :class="'relation-' + check.relation">{{ relationLabel(check.relation) }}</span> {{ check.part_text }}</p>
              <blockquote v-if="check.excerpt" class="excerpt">{{ check.excerpt }}</blockquote>
            </div>
          </details>
          <p class="quality-reason">来源说明：{{ item.quality_reason || '未提供' }}</p>
          <p class="raw-url">{{ safeExternalUrl(item.url) ?? (item.source_type === 'knowledge' ? '未填写外部出处；已保存本地原文片段与资料版本。' : '来源地址未通过 HTTP(S) 校验') }}</p>
        </article>
      </section>
    </div>
  </div>
  <div v-else class="evidence-empty"><strong>本次暂无可展示的证据</strong><p>可展开检索过程排查，或重新检索。缺少证据不代表声明正确，也不能作为判错依据。</p></div>
</template>
