<script setup lang="ts">
import { computed, onMounted } from 'vue'
import ClaimCard from './components/ClaimCard.vue'
import SourceTextPanel from './components/SourceTextPanel.vue'
import { usePrintReport } from './composables/usePrintReport'
import { useTaskSource } from './composables/useTaskSource'
import { useTaskNavigation } from './composables/useTaskNavigation'
import CoverageSummary from './components/CoverageSummary.vue'
import RetrievalQueue from './components/RetrievalQueue.vue'
import { useClaimFilters } from './composables/useClaimFilters'
import { useServiceStatus } from './composables/useServiceStatus'
import { useVerificationTask } from './composables/useVerificationTask'
import { percent } from './utils/security'

const session = useVerificationTask()
const { text, taskId, task, claims, details, activeDetail, retryingClaim, removingClaim, errorMessage, formError, phase, canExport, canRefresh, submit, exportReport, stopPolling, resumePolling, loadDetail, retry, editClaimText, removeClaim } = session
const { selectedClaimId, restoreLocation, openClaim, returnToReport, newTask } = useTaskNavigation(session)
const { sourceText, sourceError, sourceLoading, reloadSource } = useTaskSource(taskId)
const { filter, sortRisk, filteredClaims } = useClaimFilters(claims)
const serviceStatus = useServiceStatus()
const riskCount = computed(() => task.value ? task.value.label_counts.incorrect + task.value.label_counts.disputed : 0)
const hasLiveWork = computed(() => !!taskId.value && (!task.value || task.value.status === 'created' || task.value.status === 'running'))
const verificationCoverage = computed(() => percent(task.value?.coverage.verification_coverage))
const stateCounts = computed(() => {
  const counts = { pending: 0, extracting: 0, retrieving: 0, judging: 0, done: 0, failed: 0, unchecked: 0 }
  for (const claim of claims.value) counts[claim.state] += 1
  return counts
})
const stateCountItems = computed(() => [
  ['pending', '等待处理'], ['extracting', '提取中'], ['retrieving', '检索中'], ['judging', '判断中'], ['done', '已完成'], ['failed', '处理失败'], ['unchecked', '未核验'],
].map(([key, label]) => ({ key, label, count: stateCounts.value[key as keyof typeof stateCounts.value] })).filter(item => item.count > 0))
const statusLine = computed(() => {
  if (!task.value) return '正在创建核验任务…'
  if (hasLiveWork.value && phase.value === 'report') return '页面刷新已暂停；后台任务可能仍在继续。'
  if (task.value.status === 'created') return '任务已创建，等待开始处理…'
  if (task.value.status === 'running' && stateCounts.value.retrieving > 0) return '正在检索证据；已完成结果会陆续显示。'
  if (task.value.status === 'running') return '正在更新已有结果；已完成结果会陆续显示。'
  if (task.value.status === 'partial') return '已有部分结果，未完成或失败的声明不会被当作反证。'
  if (task.value.status === 'interrupted') return '服务重启使任务中断，已保存的结果仍可查看。'
  if (task.value.status === 'failed') return '任务遇到技术失败，未产生的核验结果不代表结论为假。'
  return '本次任务的可用结果已汇总。'
})
onMounted(() => { void restoreLocation() })

const { printReport } = usePrintReport()

function fillExample(): void {
  text.value = '研究显示，全球平均气温在过去十年持续上升。某项发表于 2023 年的研究证明，所有城市都将在 2030 年前实现碳中和。该政策将使相关行业的就业人数增长 50%。'
  formError.value = null
}
function editClaim(claimId: string, normalizedClaim: string): void { void editClaimText(claimId, normalizedClaim) }
function deleteClaim(claimId: string): void {
  if (window.confirm('删除这条声明？它会从汇总和导出报告中移除。')) void removeClaim(claimId)
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar"><div class="brand"><span class="brand-mark" aria-hidden="true">✓</span><div><strong>可信度验证台</strong><small>Evidence review workspace</small></div></div><span class="security-note">证据驱动 · 审慎表达</span></header>
    <main>
      <section v-if="selectedClaimId" class="claim-page" aria-labelledby="claim-page-title"><button type="button" class="secondary-button claim-page-back" @click="returnToReport">← 返回核验结果</button><button type="button" class="secondary-button" @click="printReport">打印当前证据</button><div class="claim-page-intro"><p class="eyebrow">Web search results</p><h1 id="claim-page-title">声明详情与网页检索</h1><p>查看这条信息的检索来源、摘要和判断依据。</p></div><details v-if="sourceText" class="source-detail-context"><summary>在原文中定位这条声明</summary><SourceTextPanel :text="sourceText" :claims="claims" :selected-claim-id="selectedClaimId" @open="openClaim" /></details><p v-if="errorMessage" class="global-error" role="alert">{{ errorMessage }}</p><ClaimCard v-if="details[selectedClaimId]" :claim="details[selectedClaimId]" :detail="details[selectedClaimId]" :loading="activeDetail === selectedClaimId" :retrying="retryingClaim === selectedClaimId" :retry-disabled="phase === 'processing' || hasLiveWork || retryingClaim !== null" :editable="!hasLiveWork" :remove-disabled="removingClaim !== null" standalone @retry="retry" @edit="editClaim" @remove="deleteClaim" /><div v-else class="loading-panel">{{ retryingClaim === selectedClaimId ? '正在重新检索，完成后更新证据…' : errorMessage ? '暂时无法显示这条声明。' : '正在读取网页检索结果…' }}<button v-if="errorMessage && activeDetail !== selectedClaimId && retryingClaim !== selectedClaimId" type="button" class="secondary-button claim-page-reload" @click="loadDetail(selectedClaimId)">重新加载</button></div></section>
      <section v-else-if="phase === 'input'" class="landing" aria-labelledby="landing-title">
        <div class="landing-copy"><p class="eyebrow">AI answer verification</p><h1 id="landing-title">让重要结论，<em>经得起追问。</em></h1><p class="lead">把 AI 回答拆成可核验的事实声明，保留来源、限定条件与不确定性。系统展示证据覆盖，而不替你编造确定答案。</p><div class="workflow" aria-label="核验流程"><div><span>01</span><b>提取声明</b><p>识别可检验的原子事实</p></div><div><span>02</span><b>检索证据</b><p>聚合来源并标记失败</p></div><div><span>03</span><b>审慎呈现</b><p>显示覆盖、风险与缺口</p></div></div><div class="trust-notes"><span>不把技术失败当作反证</span><span>支持指数不是正确概率</span><span>达到声明上限会明确提示</span></div></div>
        <form class="input-card" @submit.prevent="submit"><div class="input-card-heading"><div><p class="eyebrow">开始一次核验</p><h2>粘贴待核验内容</h2></div><span class="limit-badge">上限 20,000 字符</span></div><label class="sr-only" for="verification-input">待核验文本</label><textarea id="verification-input" v-model="text" maxlength="20000" placeholder="将 ChatGPT、DeepSeek 或其他模型的回答粘贴到这里…" aria-describedby="input-guidance input-error"></textarea><div class="input-meta"><span>{{ text.length.toLocaleString() }} / 20,000</span><span>本次最多处理 15 条声明</span></div><p id="input-guidance" class="privacy-hint">示例只会填入编辑框，需由你主动提交后才创建任务。</p><p v-if="formError || errorMessage" id="input-error" class="form-error" role="alert">{{ formError || errorMessage }}</p><div class="input-actions"><button class="primary-button" type="submit">开始核验 <span aria-hidden="true">→</span></button><button class="secondary-button" type="button" @click="fillExample">填入示例</button></div><p v-if="serviceStatus" class="service-hint" :class="{ 'service-alert': !serviceStatus.ready }">{{ serviceStatus.message }}</p></form>
      </section>
      <section v-else class="report-view" aria-live="polite"><div class="report-toolbar"><div><p class="eyebrow">Verification task</p><h1>核验结果</h1><p v-if="task" class="task-id">任务 {{ task.task_id }} · 输入 {{ task.input_char_count.toLocaleString() }} 字符</p></div><div class="toolbar-actions"><button class="secondary-button" type="button" @click="newTask">新建任务</button><button v-if="hasLiveWork && phase === 'processing'" class="secondary-button" type="button" @click="stopPolling">停止刷新</button><button v-else-if="canRefresh" class="secondary-button" type="button" @click="resumePolling">{{ hasLiveWork ? '恢复刷新' : '刷新结果' }}</button><button v-if="task" class="secondary-button" type="button" @click="printReport">打印报告</button><template v-if="canExport && task"><button class="secondary-button" type="button" @click="exportReport('json')">导出 JSON</button><button class="primary-button small" type="button" @click="exportReport('md')">导出 Markdown</button></template></div></div><p v-if="errorMessage" class="global-error" role="alert">{{ errorMessage }}</p><template v-if="task"><section class="process-panel" aria-labelledby="process-title"><div><p class="eyebrow">Live task state</p><h2 id="process-title">{{ statusLine }}</h2></div><div class="process-stats"><div><strong>{{ task.claims_processed }}</strong><span>已处理</span></div><div><strong>{{ riskCount }}</strong><span>风险提示</span></div><div><strong>{{ verificationCoverage }}</strong><span>核验覆盖</span></div></div><div v-if="stateCountItems.length" class="process-states" aria-label="已返回声明的处理状态"><span v-for="item in stateCountItems" :key="item.key">{{ item.label }} {{ item.count }}</span></div><RetrievalQueue v-if="phase === 'processing' && hasLiveWork" :claims="claims" /><p class="process-caption">已完成的声明会陆续显示，可随时查看证据。</p></section><SourceTextPanel v-if="sourceText" :text="sourceText" :claims="claims" :selected-claim-id="selectedClaimId" @open="openClaim" /><p v-else-if="sourceLoading" class="process-caption">正在加载原文…</p><p v-else-if="sourceError" class="notice warning">原文暂不可用：{{ sourceError }} <button type="button" class="text-button" @click="reloadSource">重新加载原文</button></p><div class="report-content"><CoverageSummary :task="task" /><section class="claims-section" aria-labelledby="claims-title"><div class="section-heading"><div><p class="eyebrow">Claims review</p><h2 id="claims-title">已返回声明 <span>{{ filteredClaims.length }}<small> / {{ claims.length }}</small></span></h2></div><div class="filters"><select v-model="filter" aria-label="按标签筛选"><option value="all">全部标签</option><option value="credible">可信</option><option value="disputed">存在争议</option><option value="incorrect">错误</option><option value="evidence_insufficient">证据不足</option><option value="not_applicable">不适用</option></select><label><input v-model="sortRisk" type="checkbox" /> 风险优先</label></div></div><div v-if="filteredClaims.length" class="claims-list"><ClaimCard v-for="claim in filteredClaims" :key="claim.claim_id" :claim="claim" :detail="details[claim.claim_id] ?? null" :loading="activeDetail === claim.claim_id" :retrying="retryingClaim === claim.claim_id" :retry-disabled="phase === 'processing' || hasLiveWork || retryingClaim !== null" :editable="!hasLiveWork" :remove-disabled="removingClaim !== null" @open="openClaim" @retry="retry" @edit="editClaim" @remove="deleteClaim" /></div><div v-else class="empty-state">{{ claims.length ? '没有符合当前筛选条件的声明。' : hasLiveWork && phase === 'report' ? '尚无声明，恢复刷新查看结果。' : hasLiveWork ? '尚未返回可展示的声明；页面会在获得真实结果后更新。' : '本次任务未返回可展示的声明。' }}</div></section></div></template><div v-else class="loading-panel">正在连接任务…</div><footer class="disclaimer">本系统基于当前可访问证据提供内容质检，不承诺绝对真伪。证据不足、来源失败或未核验声明均不构成反证；支持指数不等于事实为真的概率。</footer></section>
    </main>
  </div>
</template>

