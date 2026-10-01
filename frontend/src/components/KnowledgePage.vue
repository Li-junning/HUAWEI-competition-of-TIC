<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import FileTypeIcon from './FileTypeIcon.vue'
import { ApiError, deleteKnowledgeDocument, getKnowledgeDocument, getKnowledgeDocuments, getKnowledgeStatus, importKnowledgeDocument, reindexKnowledge, searchKnowledge } from '../api/client'
import type { KnowledgeDocument, KnowledgeHit, KnowledgeStatus } from '../types/api'
import { formatDate, safeExternalUrl } from '../utils/security'
import { isSubmitShortcut } from '../utils/keyboard'
import { filePresentation } from '../utils/fileType'

const props = defineProps<{ initialDocument?: string | null }>()
defineEmits<{ back: [] }>()
const documents = ref<KnowledgeDocument[]>([])
const status = ref<KnowledgeStatus | null>(null)
const loading = ref(true)
const busy = ref(false)
const searching = ref(false)
const error = ref('')
const notice = ref('')
const importDialog = ref<HTMLDialogElement | null>(null)
const importTrigger = ref<HTMLButtonElement | null>(null)
const importMode = ref<'file' | 'text'>('file')
const importError = ref('')
const dragging = ref(false)
const activeView = ref<'documents' | 'results'>('documents')
const documentQuery = ref('')
const documentTag = ref('')
const title = ref('')
const publisher = ref('')
const sourceUrl = ref('')
const publishedAt = ref('')
const tags = ref('')
const content = ref('')
const file = ref<File | null>(null)
const rejectedFile = ref<File | null>(null)
const fileProblem = ref<'format' | 'size' | null>(null)
const displayedFile = computed(() => file.value ?? rejectedFile.value)
const selectedFormat = computed(() => filePresentation(displayedFile.value?.name))
const fileInput = ref<HTMLInputElement | null>(null)
const query = ref('')
const tag = ref('')
const hits = ref<KnowledgeHit[]>([])
const searched = ref(false)
const searchError = ref('')
const lastQuery = ref('')
const lastTag = ref('')
const resultsPanel = ref<HTMLElement | null>(null)
const importErrorPanel = ref<HTMLElement | null>(null)
const selected = ref<{ document: KnowledgeDocument; pages: string[] } | null>(null)
const sourceLoading = ref(false)
const sourcePanel = ref<HTMLElement | null>(null)
const activeDocumentId = ref<string | null>(null)
const page = ref(1)
const selection = ref<{ start: number; end: number } | null>(null)
const currentText = computed(() => selected.value?.pages[page.value - 1] ?? '')
const currentCharacters = computed(() => Array.from(currentText.value))
const highlight = computed(() => selection.value && Number.isInteger(selection.value.start) && Number.isInteger(selection.value.end) && selection.value.start >= 0 && selection.value.end <= currentCharacters.value.length && selection.value.end > selection.value.start ? selection.value : null)
const knownTags = computed(() => [...new Set(documents.value.flatMap(doc => doc.tags))])
const filteredDocuments = computed(() => {
  const needle = documentQuery.value.trim().toLocaleLowerCase()
  return documents.value.filter(doc => (!documentTag.value || doc.tags.includes(documentTag.value))
    && (!needle || [doc.title, doc.publisher ?? '', ...doc.tags].join(' ').toLocaleLowerCase().includes(needle)))
})
let disposed = false
let documentRequest = 0
onUnmounted(() => { disposed = true; documentRequest += 1 })

function message(reason: unknown): string { return reason instanceof Error ? reason.message : '操作未完成，请稍后重试。' }
function openImport(mode: 'file' | 'text' = 'file'): void {
  if (busy.value || searching.value) return
  importMode.value = mode
  importError.value = ''
  importDialog.value?.showModal()
}
function closeImport(): void { if (!busy.value) importDialog.value?.close() }
function cancelImport(event: Event): void { if (busy.value) event.preventDefault() }
function scrollToImportError(): void { void nextTick(() => importErrorPanel.value?.scrollIntoView({ block: 'nearest' })) }
function handleSearchKeydown(event: KeyboardEvent): void {
  if (!isSubmitShortcut(event)) return
  event.preventDefault()
  void search()
}
function documentFormat(doc: KnowledgeDocument): string {
  return doc.filename?.split('.').pop()?.toUpperCase() || '文本'
}
async function refresh(): Promise<void> {
  loading.value = true
  try {
    const [list, state] = await Promise.all([getKnowledgeDocuments(), getKnowledgeStatus()])
    if (disposed) return
    documents.value = list.items
    status.value = state
  } catch (reason) { if (!disposed) error.value = message(reason) }
  finally { if (!disposed) loading.value = false }
}
function clearFile(): void {
  file.value = null; rejectedFile.value = null; fileProblem.value = null
  if (fileInput.value) fileInput.value.value = ''
}
function removeFile(): void { clearFile(); importError.value = ''; void nextTick(() => fileInput.value?.focus()) }
function replaceFile(): void { if (!busy.value && !searching.value) fileInput.value?.click() }
function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.ceil(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`
}
function pickFile(event: Event): void {
  const chosen = (event.target as HTMLInputElement).files?.[0]
  acceptFile(chosen)
}
function acceptFile(chosen?: File): void {
  importError.value = ''
  if (!chosen) { clearFile(); return }
  const problem = chosen.size > 2 * 1024 * 1024 ? 'size' : !/\.(txt|md|pdf|docx)$/i.test(chosen.name) ? 'format' : null
  if (problem) {
    clearFile(); rejectedFile.value = chosen; fileProblem.value = problem
    importError.value = problem === 'size' ? '单个文件最多 2 MiB。'
      : filePresentation(chosen.name).kind === 'word' ? '旧版 .doc 请先在 Word 中另存为 .docx 后添加。' : '支持 Word（.docx）、TXT、Markdown 和可提取文字的 PDF。'
    return
  }
  rejectedFile.value = null; fileProblem.value = null
  file.value = chosen
  if (!title.value.trim()) title.value = chosen.name.replace(/\.[^.]+$/, '').slice(0, 160)
}
function dropFile(event: DragEvent): void {
  dragging.value = false
  if (busy.value || searching.value) return
  acceptFile(event.dataTransfer?.files[0])
}
function encodeFile(chosen: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(new Error('文件读取失败，请重新选择。'))
    reader.readAsDataURL(chosen)
  })
}
async function save(): Promise<void> {
  if (busy.value || searching.value) return
  importError.value = ''; notice.value = ''
  const chosenFile = importMode.value === 'file' ? file.value : null
  if (importMode.value === 'file' && fileProblem.value) { scrollToImportError(); return }
  if (!title.value.trim() || (importMode.value === 'file' ? !chosenFile : !content.value.trim())) { importError.value = '请填写资料标题，并上传文件或粘贴正文。'; scrollToImportError(); return }
  busy.value = true
  let saved = false
  try {
    const payload = { title: title.value.trim(), publisher: publisher.value.trim() || null,
      source_url: sourceUrl.value.trim() || null, published_at: publishedAt.value || null,
      tags: [...new Set(tags.value.split(/[,，]/).map(value => value.trim()).filter(Boolean))] }
    const imported = await importKnowledgeDocument(chosenFile
      ? { ...payload, filename: chosenFile.name, file_base64: await encodeFile(chosenFile) }
      : { ...payload, content: content.value })
    if (disposed) return
    saved = true
    notice.value = `已保存“${imported.title}”，建立 ${imported.chunk_count} 个检索片段。`
    title.value = ''; publisher.value = ''; sourceUrl.value = ''; publishedAt.value = ''; tags.value = ''; content.value = ''; clearFile()
    hits.value = []; searched.value = false
    activeView.value = 'documents'; documentQuery.value = ''; documentTag.value = ''
    importDialog.value?.close()
    await refresh()
  } catch (reason) {
    if (!disposed) {
      const uncertain = reason instanceof ApiError && (reason.code === 'REQUEST_TIMEOUT' || reason.code === 'NETWORK_ERROR')
      importError.value = message(reason) + (uncertain ? ' 请先关闭面板并刷新资料列表确认是否已保存，再决定是否重试。' : '')
      scrollToImportError()
    }
  }
  finally {
    if (!disposed) {
      busy.value = false
      if (saved) { await nextTick(); importTrigger.value?.focus({ preventScroll: true }) }
    }
  }
}
async function search(): Promise<void> {
  if (searching.value || busy.value || !query.value.trim()) return
  const submittedQuery = query.value.trim()
  const submittedTag = tag.value
  searching.value = true; searchError.value = ''
  try {
    const result = await searchKnowledge(submittedQuery, submittedTag || null)
    if (disposed) return
    hits.value = result.items; status.value = result.status; searched.value = true
    activeView.value = 'results'
    lastQuery.value = submittedQuery; lastTag.value = submittedTag
    await nextTick()
    if (!disposed && window.matchMedia('(max-width: 800px)').matches) {
      resultsPanel.value?.focus({ preventScroll: true })
      resultsPanel.value?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
    }
  } catch (reason) { if (!disposed) searchError.value = message(reason) }
  finally { if (!disposed) searching.value = false }
}
async function viewDocument(id: string, hit?: KnowledgeHit): Promise<void> {
  const request = ++documentRequest
  activeDocumentId.value = id
  sourceLoading.value = true; error.value = ''; selected.value = null
  selection.value = null
  try {
    const result = await getKnowledgeDocument(id)
    if (disposed || request !== documentRequest) return
    selected.value = result; page.value = hit?.page ?? 1
    if (hit) selection.value = { start: hit.char_start, end: hit.char_end }
    else if (id === props.initialDocument) {
      const params = new URLSearchParams(window.location.search)
      const requestedPage = Number(params.get('kb_page') ?? 1)
      page.value = Number.isInteger(requestedPage) && requestedPage >= 1 && requestedPage <= result.pages.length ? requestedPage : 1
      if (params.has('kb_start') && params.has('kb_end')) selection.value = { start: Number(params.get('kb_start')), end: Number(params.get('kb_end')) }
    }
    await nextTick()
    sourcePanel.value?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
  } catch (reason) { if (!disposed && request === documentRequest) error.value = message(reason) }
  finally { if (!disposed && request === documentRequest) sourceLoading.value = false }
}
async function remove(doc: KnowledgeDocument): Promise<void> {
  if (busy.value || !window.confirm(`删除“${doc.title}”？后续核验将不再检索它；已有报告中的证据片段会保留。`)) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await deleteKnowledgeDocument(doc.document_id)
    if (disposed) return
    if (activeDocumentId.value === doc.document_id) { documentRequest += 1; selected.value = null; sourceLoading.value = false }
    hits.value = []; searched.value = false
    notice.value = '资料已从知识库删除。'
    await refresh()
  } catch (reason) { if (!disposed) error.value = message(reason) }
  finally { if (!disposed) busy.value = false }
}
async function reindex(): Promise<void> {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await reindexKnowledge()
    if (disposed) return
    notice.value = `已补建 ${result.indexed} 个向量；还有 ${result.remaining} 个待补建。`
    await refresh()
  } catch (reason) { if (!disposed) error.value = message(reason) }
  finally { if (!disposed) busy.value = false }
}
onMounted(async () => { await refresh(); if (!disposed && props.initialDocument) await viewDocument(props.initialDocument) })
</script>

<template>
  <section class="knowledge-page" aria-labelledby="knowledge-title">
    <header class="kb-page-heading">
      <div><p class="eyebrow">YOUR EVIDENCE LIBRARY</p><h1 id="knowledge-title">我的知识库<span class="kb-title-dot" aria-hidden="true"></span></h1><p class="kb-page-description">让你的参考资料，成为每次核验的证据。</p></div>
      <div class="kb-heading-actions"><button type="button" class="secondary-button" @click="$emit('back')"><AppIcon name="arrow" class="kb-back-arrow" />返回核验</button><button ref="importTrigger" type="button" class="primary-button" :disabled="busy || searching" @click="openImport()"><AppIcon name="plus" />添加资料</button></div>
    </header>
    <p v-if="error" class="global-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="kb-notice" role="status"><AppIcon name="check" />{{ notice }}</p>

    <div class="kb-overview" aria-label="知识库概况">
      <section class="kb-stat-card"><div class="kb-stat-top"><span>已保存资料</span><span class="kb-stat-icon"><AppIcon name="library" /></span></div><strong>{{ status ? status.document_count.toLocaleString() : '—' }}<small>份</small></strong><p>为核验积累可追溯的来源</p></section>
      <section class="kb-stat-card"><div class="kb-stat-top"><span>可检索证据</span><span class="kb-stat-icon"><AppIcon name="layers" /></span></div><strong>{{ status ? status.chunk_count.toLocaleString() : '—' }}<small>个片段</small></strong><p>从资料正文中提取的检索片段</p></section>
      <section class="kb-stat-card kb-engine-card"><div class="kb-stat-top"><span>检索方式</span><span class="kb-stat-icon"><AppIcon name="search" /></span></div><strong class="kb-mode">{{ status ? status.mode === 'hybrid' ? '混合检索' : '关键词检索' : '等待确认' }}<span v-if="status?.semantic_ready" class="kb-ready-badge"><i aria-hidden="true"></i>语义就绪</span></strong><p>{{ status ? '已建立 ' + status.indexed_chunks.toLocaleString() + ' 个向量' : '正在读取检索状态…' }}</p></section>
    </div>
    <p v-if="status" class="kb-engine-note"><AppIcon name="info" />{{ status.message }}<button v-if="status.semantic_ready && status.indexed_chunks < status.chunk_count" class="text-button" type="button" :disabled="busy || searching" @click="reindex">{{ busy ? '正在处理…' : '补建语义索引' }}<AppIcon name="arrow" /></button></p>

    <div class="kb-workspace">
      <section class="kb-card kb-library" aria-labelledby="kb-library-title">
        <div class="kb-library-heading"><div><h2 id="kb-library-title">资料与证据</h2><p>管理参考资料，也可以直接追溯检索片段。</p></div><button type="button" class="kb-refresh-button" :disabled="loading || busy" @click="error = ''; refresh()"><AppIcon name="refresh" :class="{ 'kb-spinning': loading }" />刷新</button></div>
        <div class="kb-view-tabs" role="group" aria-label="切换资料与检索结果"><button type="button" :class="{ active: activeView === 'documents' }" :aria-pressed="activeView === 'documents'" aria-controls="kb-documents-view" @click="activeView = 'documents'"><AppIcon name="library" />全部资料<span>{{ documents.length }}</span></button><button type="button" :class="{ active: activeView === 'results' }" :aria-pressed="activeView === 'results'" aria-controls="kb-results-view" @click="activeView = 'results'"><AppIcon name="search" />检索结果<span v-if="searched">{{ hits.length }}</span></button></div>

        <div v-if="activeView === 'documents'" id="kb-documents-view" :aria-busy="loading">
          <div class="kb-list-filters"><div class="kb-filter-input"><AppIcon name="search" /><label class="sr-only" for="kb-document-query">查找已保存资料</label><input id="kb-document-query" v-model="documentQuery" :disabled="loading || !documents.length" placeholder="搜索标题、发布方或标签…" /><button v-if="documentQuery" type="button" aria-label="清空资料搜索" @click="documentQuery = ''"><AppIcon name="close" /></button></div><select v-model="documentTag" :disabled="loading || !knownTags.length" aria-label="筛选资料标签"><option value="">全部标签</option><option v-for="item in knownTags" :key="item" :value="item">{{ item }}</option></select></div>
          <div v-if="loading" class="kb-list-loading" role="status"><span>正在读取资料…</span><div v-for="index in 3" :key="index" class="kb-skeleton-row" aria-hidden="true"><i></i><div><span></span><span></span></div></div></div>
          <div v-else-if="!documents.length" class="kb-empty">
            <div class="kb-empty-art" aria-hidden="true"><span class="kb-art-page kb-art-back"><AppIcon name="file" /></span><span class="kb-art-page"><AppIcon name="file" /></span><span class="kb-art-seal"><AppIcon name="plus" /></span></div>
            <h3>从第一份可信资料开始</h3><p>技术白皮书、研究论文或政策文件，<br />都可以成为你下一次核验的参考依据。</p>
            <div class="kb-empty-actions"><button type="button" class="primary-button" :disabled="busy || searching" @click="openImport('file')"><AppIcon name="upload" />上传资料</button><button type="button" class="secondary-button" :disabled="busy || searching" @click="openImport('text')"><AppIcon name="file" />粘贴正文</button></div>
            <div class="kb-formats"><span>Word</span><span>PDF</span><span>TXT</span><span>Markdown</span><small>单文件最多 2 MiB</small></div>
          </div>
          <div v-else-if="!filteredDocuments.length" class="kb-filter-empty"><AppIcon name="search" /><h3>没有找到匹配的资料</h3><p>试试其他关键词，或调整分类标签。</p><button type="button" class="secondary-button" @click="documentQuery = ''; documentTag = ''">清除筛选</button></div>
          <ul v-else class="kb-document-list" aria-label="已保存资料">
            <li v-for="doc in filteredDocuments" :key="doc.document_id" class="kb-document">
              <span class="kb-document-icon"><FileTypeIcon :filename="doc.filename" /><span class="sr-only">{{ documentFormat(doc) }} 资料</span></span>
              <div class="kb-document-info"><h3 :title="doc.title">{{ doc.title }}</h3><p>{{ doc.publisher || '未填写发布方' }}<span>·</span>{{ doc.char_count.toLocaleString() }} 字符</p><div class="kb-tags"><span v-for="item in doc.tags" :key="item">{{ item }}</span><small>{{ formatDate(doc.created_at) }} 导入</small></div></div>
              <div class="kb-document-count"><strong>{{ doc.chunk_count }}</strong><small>证据片段</small></div>
              <div class="kb-document-actions"><button type="button" class="kb-view-document" @click="viewDocument(doc.document_id)">查看原文<AppIcon name="chevron" /></button><button type="button" class="kb-delete" :disabled="busy || searching" :aria-label="'删除资料：' + doc.title" title="删除资料" @click="remove(doc)"><AppIcon name="trash" /></button></div>
            </li>
          </ul>
          <div class="kb-list-footer"><AppIcon name="shield" /><span>资料保存在本机，新建核验任务会自动检索你的知识库。</span><span v-if="documents.length" class="kb-visible-count">{{ filteredDocuments.length }} / {{ documents.length }} 份</span></div>
        </div>

        <div v-else id="kb-results-view" ref="resultsPanel" class="kb-results-view" tabindex="-1" role="region" aria-label="知识库检索结果" :aria-busy="searching">
          <p v-if="searched" class="kb-query-summary">本次检索：<strong>{{ lastQuery }}</strong><span v-if="lastTag">标签 · {{ lastTag }}</span></p>
          <div v-if="!searched" class="kb-filter-empty kb-search-empty"><span class="kb-empty-search-icon"><AppIcon name="search" /></span><h3>证据，从一个问题开始</h3><p>在检索框输入问题或待核验的说法，<br />查看匹配片段，并追溯到资料原文。</p></div>
          <div v-else-if="!hits.length" class="kb-filter-empty"><AppIcon name="search" /><h3>未找到相关证据</h3><p>换个表达，或添加更多资料再试。<br />未命中不能作为判定说法为假的依据。</p></div>
          <template v-else><p class="kb-results-caption">找到 {{ hits.length }} 个相关片段 · 匹配与排序不代表说法正确</p><article v-for="(hit, index) in hits" :key="hit.chunk_id" class="kb-hit"><div class="kb-hit-heading"><span class="kb-hit-number">{{ String(index + 1).padStart(2, '0') }}</span><h3>{{ hit.title }}</h3><span class="kb-match-badge">{{ hit.matched_by.includes('semantic') ? (hit.matched_by.includes('keyword') ? '关键词 + 语义' : '语义匹配') : '关键词匹配' }}</span></div><p class="kb-help">{{ hit.publisher || '发布方未填写' }} · {{ hit.page ? '第 ' + hit.page + ' 页 · ' : '' }}正文字符 {{ hit.char_start }}–{{ hit.char_end }}</p><blockquote>{{ hit.excerpt }}</blockquote><button class="kb-result-link" type="button" @click="viewDocument(hit.document_id, hit)">定位到原文<AppIcon name="arrow" /></button></article></template>
        </div>
      </section>

      <aside class="kb-search-sidebar" aria-labelledby="kb-search-title">
        <section class="kb-card kb-search-card">
          <span class="kb-search-symbol"><AppIcon name="search" /></span><p class="eyebrow">FIND YOUR EVIDENCE</p><h2 id="kb-search-title">检索你的证据</h2><p class="kb-search-description">从已保存的资料里，找到支持判断的线索。</p>
          <form @submit.prevent="search"><label class="sr-only" for="kb-query">检索问题或待验证声明</label><textarea id="kb-query" v-model="query" rows="4" maxlength="2000" :disabled="searching || busy" :aria-describedby="searchError ? 'kb-search-note kb-search-error' : 'kb-search-note'" placeholder="输入问题或待核验的说法…&#10;&#10;例如：这项技术有哪些适用条件？" @keydown="handleSearchKeydown" /><p class="kb-search-shortcut keyboard-shortcut">Ctrl / ⌘ + Enter 检索</p><div class="kb-search-actions"><select v-model="tag" :disabled="searching || busy" aria-label="按分类标签检索"><option value="">全部标签</option><option v-for="item in knownTags" :key="item" :value="item">{{ item }}</option></select><button class="primary-button" type="submit" :disabled="searching || busy || !query.trim()"><AppIcon name="search" />{{ searching ? '检索中…' : '检索证据' }}</button></div><p v-if="searchError" id="kb-search-error" class="form-error kb-search-error" role="alert">{{ searchError }}</p></form>
          <p id="kb-search-note" class="kb-search-note"><AppIcon name="info" />检索用于寻找证据；命中和排序分数均不代表说法正确。</p>
        </section>
        <section class="kb-guide"><h3><AppIcon name="sparkles" />让资料更容易被找到</h3><ul><li><AppIcon name="check" />优先保留主体、时间、单位与条件</li><li><AppIcon name="check" />补充发布方与原始出处，方便复核</li><li><AppIcon name="check" />用标签整理同一主题的资料</li></ul></section>
      </aside>
    </div>

    <section v-if="sourceLoading || selected" ref="sourcePanel" class="kb-card kb-source" aria-label="资料原文">
      <p v-if="sourceLoading" role="status">正在读取原文…</p>
      <template v-else-if="selected"><div class="kb-source-heading"><div><p class="eyebrow">ORIGINAL SOURCE</p><h2>{{ selected.document.title }}</h2></div><button type="button" class="secondary-button" @click="selected = null; documentRequest += 1"><AppIcon name="close" />收起原文</button></div><p class="kb-help">{{ selected.document.publisher || '发布方未填写' }} · 发布 {{ formatDate(selected.document.published_at) }} · 导入 {{ formatDate(selected.document.created_at) }} <a v-if="safeExternalUrl(selected.document.source_url)" :href="safeExternalUrl(selected.document.source_url) ?? undefined" target="_blank" rel="noopener noreferrer">打开原始出处 ↗</a></p><details class="kb-provenance"><summary>资料版本与复核信息</summary><p class="kb-hash">SHA-256：{{ selected.document.content_hash }}</p></details><label v-if="selected.pages.length > 1" class="kb-page-picker">页码 <select v-model="page" @change="selection = null"><option v-for="(_, index) in selected.pages" :key="index" :value="index + 1">第 {{ index + 1 }} 页</option></select></label><pre v-if="highlight" class="kb-original">{{ currentCharacters.slice(0, highlight.start).join('') }}<mark>{{ currentCharacters.slice(highlight.start, highlight.end).join('') }}</mark>{{ currentCharacters.slice(highlight.end).join('') }}</pre><pre v-else class="kb-original">{{ currentText || '这一页没有提取到文字。' }}</pre></template>
    </section>
    <footer class="kb-footnote"><AppIcon name="info" /><p>新导入或删除资料只影响后续核验，已有报告保留当时的证据片段与资料版本。使用新资料时，请新建任务或重新检索声明。</p></footer>

    <dialog ref="importDialog" class="kb-import-dialog" aria-labelledby="kb-import-title" aria-describedby="kb-import-description" @cancel="cancelImport" @close="dragging = false">
      <div class="kb-modal-heading"><div><p class="eyebrow">ADD TO YOUR LIBRARY</p><h2 id="kb-import-title">添加参考资料</h2><p id="kb-import-description">保存一份资料，为之后的核验补充依据。</p></div><button type="button" class="kb-dialog-close" :disabled="busy" aria-label="关闭添加资料面板" @click="closeImport"><AppIcon name="close" /></button></div>
      <form class="kb-import" @submit.prevent="save">
        <fieldset :disabled="busy || searching">
          <div class="kb-import-tabs" role="group" aria-label="资料导入方式"><button type="button" :class="{ active: importMode === 'file' }" :aria-pressed="importMode === 'file'" @click="importMode = 'file'; importError = ''"><AppIcon name="upload" />上传文件</button><button type="button" :class="{ active: importMode === 'text' }" :aria-pressed="importMode === 'text'" @click="importMode = 'text'; importError = ''"><AppIcon name="file" />粘贴正文</button></div>
          <div v-show="importMode === 'file'" class="kb-file-picker" :class="{ 'is-dragging': dragging }" @dragover.prevent="dragging = !busy && !searching" @dragleave.prevent="dragging = false" @drop.prevent="dropFile">
            <input id="kb-file" ref="fileInput" class="sr-only" type="file" accept=".docx,.pdf,.txt,.md" aria-label="选择参考资料文件" :tabindex="displayedFile ? -1 : 0" @change="pickFile" />
            <div v-if="displayedFile" class="kb-file-card" :class="{ 'is-rejected': !!fileProblem, 'is-saving': busy }" aria-labelledby="kb-selected-filename">
              <div class="kb-file-state" role="status" aria-live="polite" aria-atomic="true">
                <span class="kb-file-state-icon"><AppIcon :name="busy ? 'refresh' : fileProblem ? 'info' : 'check'" :class="{ 'kb-spinning': busy }" /></span>
                <div><strong>{{ busy ? '正在保存资料' : fileProblem === 'size' ? '文件大小超出限制' : fileProblem ? '暂不支持此格式' : '文件已准备好' }}</strong><p>{{ busy ? '正在处理文件并建立检索索引，请稍候。' : fileProblem === 'size' ? '请选择不超过 2 MiB 的文件，或拆分后再添加。' : fileProblem ? selectedFormat.kind === 'word' ? '旧版 .doc 请在 Word 中另存为 .docx 后添加。' : '请选择 Word（.docx）、PDF、TXT 或 Markdown 文件。' : '点击下方「保存到知识库」完成添加。' }}</p></div>
                <span class="kb-file-state-badge">{{ busy ? '保存中' : fileProblem ? '需更换' : '待保存' }}</span>
              </div>
              <div class="kb-file-summary"><FileTypeIcon :filename="displayedFile.name" size="large" /><div><strong id="kb-selected-filename">{{ displayedFile.name }}</strong><p>{{ selectedFormat.label }}<span>·</span>{{ formatFileSize(displayedFile.size) }}</p></div></div>
              <div class="kb-file-actions"><button type="button" class="secondary-button" @click="replaceFile"><AppIcon name="refresh" />更换文件</button><button type="button" class="kb-remove-file" @click="removeFile"><AppIcon name="trash" />移除文件</button></div>
            </div>
            <label v-else for="kb-file" class="kb-upload"><span class="kb-upload-icon"><AppIcon name="upload" /></span><strong>点击选择文件，或拖拽到这里</strong><span>支持 Word（.docx）、PDF、TXT、Markdown</span><small>单文件最多 2 MiB · PDF 最多 100 页</small></label>
            <p class="kb-help">Word 提取正文与表格文字；旧版 .doc 需另存为 .docx。TXT / Markdown 使用 UTF-8；图片文字需先做 OCR。</p>
          </div>
          <label for="kb-title">资料标题 <span>必填</span></label><input id="kb-title" v-model="title" required maxlength="160" placeholder="例如：产品技术白皮书" />
          <div v-if="importMode === 'text'"><label for="kb-content">资料正文 <span>必填</span></label><textarea id="kb-content" v-model="content" maxlength="100000" rows="6" placeholder="粘贴资料正文，保留主体、时间、条件和单位…" /><p class="kb-help">{{ content.length.toLocaleString() }} / 100,000 字符</p></div>
          <details class="kb-optional-fields"><summary>补充来源与分类<span>选填，方便追溯</span><AppIcon name="chevron" /></summary><div class="kb-fields"><div><label for="kb-publisher">发布方 / 作者</label><input id="kb-publisher" v-model="publisher" maxlength="160" placeholder="保留原始署名" /></div><div><label for="kb-date">发布日期</label><input id="kb-date" v-model="publishedAt" type="date" /></div></div><label for="kb-url">原始出处链接</label><input id="kb-url" v-model="sourceUrl" type="url" maxlength="2048" placeholder="https://…" /><label for="kb-tags">分类标签</label><input id="kb-tags" v-model="tags" maxlength="490" placeholder="技术、论文、政策，以逗号分隔" /></details>
        </fieldset>
        <p v-if="importError" ref="importErrorPanel" class="form-error" role="alert">{{ importError }}</p>
        <p class="kb-import-privacy"><AppIcon name="shield" />资料保存到本机。启用模型判断时，命中的证据片段会发送给已配置的判断模型。</p>
        <div class="kb-modal-actions"><button type="button" class="secondary-button" :disabled="busy" @click="closeImport">取消</button><button class="primary-button" type="submit" :disabled="busy || searching || (importMode === 'file' && !file)"><AppIcon :name="busy ? 'refresh' : 'plus'" :class="{ 'kb-spinning': busy }" />{{ busy ? '正在保存与建索引…' : '保存到知识库' }}</button></div>
      </form>
    </dialog>
  </section>
</template>

<style scoped>
.knowledge-page { color: #294235; }
.knowledge-page h1 { display: flex; align-items: center; gap: 13px; margin: 0 0 11px; font-size: clamp(29px, 3.4vw, 38px); font-weight: 700; line-height: 1.3; }
.knowledge-page h2 { margin: 0; color: #294b36; font-size: 18px; font-weight: 600; }
.knowledge-page h3 { color: #34513c; font-weight: 600; overflow-wrap: anywhere; }
.knowledge-page button { display: inline-flex; align-items: center; justify-content: center; gap: 7px; }
.knowledge-page button .app-icon { width: 16px; height: 16px; }
.kb-page-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 27px; }
.kb-page-heading .eyebrow { margin-bottom: 10px; font-size: 9px; }
.kb-title-dot { width: 7px; height: 7px; border-radius: 50%; background: #9abf89; }
.kb-page-description { margin: 0; color: #6e7f71; font-size: 13px; line-height: 1.8; }
.kb-heading-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.kb-heading-actions .primary-button { min-height: 40px; padding: 10px 17px; }
.kb-back-arrow { transform: rotate(180deg); }
.kb-overview { display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 16px; }
.kb-stat-card { min-width: 0; padding: 20px 22px 17px; border: 1px solid #e1e8df; border-radius: 13px; background: #fff; }
.kb-stat-top { display: flex; align-items: center; justify-content: space-between; gap: 10px; color: #6a7d6c; font-size: 12px; }
.kb-stat-icon { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 8px; background: #f0f5ed; color: #77956a; }
.kb-stat-icon .app-icon { display: block; width: 17px; height: 17px; }
.kb-stat-card strong { display: flex; align-items: baseline; gap: 9px; margin-top: 3px; color: #294d36; font-size: 30px; font-weight: 600; letter-spacing: -.04em; line-height: 1.5; }
.kb-stat-card strong small { color: #7b8b7b; font-size: 11px; font-weight: 400; letter-spacing: 0; }
.kb-stat-card p { margin: 4px 0 0; color: #6b7d65; font-size: 12px; line-height: 1.7; }
.kb-engine-card { background: #eff5eb; border-color: #dce8d5; }
.kb-engine-card .kb-stat-icon { background: #ffffff9c; }
.kb-stat-card .kb-mode { align-items: center; flex-wrap: wrap; gap: 10px; margin-top: 12px; font-size: 22px; line-height: 1.3; letter-spacing: -.02em; }
.kb-ready-badge { display: inline-flex; align-items: center; gap: 5px; padding: 4px 7px; border: 1px solid #d7e5d0; border-radius: 20px; background: #f8fbf5; color: #66815c; font-size: 9px; font-weight: 500; white-space: nowrap; }
.kb-ready-badge i { width: 4px; height: 4px; border-radius: 50%; background: #71995e; }
.kb-engine-note { display: flex; align-items: flex-start; flex-wrap: wrap; gap: 6px; margin: 12px 2px 25px; color: #6b7d65; font-size: 11px; line-height: 1.8; }
.kb-engine-note > .app-icon { width: 13px; height: 13px; margin-top: 2px; }
.kb-engine-note .text-button { margin: 0 0 0 auto; padding: 0; font-size: 10px; }
.kb-engine-note .text-button .app-icon { width: 12px; height: 12px; }
.kb-workspace { display: grid; grid-template-columns: minmax(0, 1fr) 320px; align-items: start; gap: 22px; margin-top: 25px; }
.kb-engine-note + .kb-workspace { margin-top: 0; }
.kb-card { min-width: 0; border: 1px solid #e1e8df; border-radius: 15px; background: #fff; box-shadow: 0 3px 14px #24462b03; }
.kb-library { overflow: hidden; }
.kb-library-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 23px 24px 20px; }
.kb-library-heading p { margin: 7px 0 0; color: #6b7d65; font-size: 12px; line-height: 1.7; }
.kb-refresh-button { padding: 7px 9px; border: 1px solid #e6ece3; border-radius: 7px; background: #fafcf8; color: #698065; font-size: 11px; white-space: nowrap; }
.kb-refresh-button:hover:not(:disabled) { background: #f0f6e9; border-color: #c8d9be; }
.kb-view-tabs { display: flex; gap: 25px; padding: 0 24px; border-bottom: 1px solid #e9eee6; }
.kb-view-tabs button { position: relative; gap: 7px; min-height: 46px; padding: 0 0 12px; border: 0; background: transparent; color: #6b7d65; font-size: 13px; }
.kb-view-tabs button.active { color: #426c40; font-weight: 600; }
.kb-view-tabs button.active::after { position: absolute; right: 0; bottom: -1px; left: 0; height: 2px; border-radius: 4px; background: #668a51; content: ''; }
.kb-view-tabs button span { padding: 2px 6px; border-radius: 5px; background: #f0f4ec; color: #6e8466; font-size: 10px; }
.kb-list-filters { display: flex; gap: 10px; padding: 19px 24px 0; }
.kb-filter-input { display: flex; align-items: center; flex: 1; min-width: 0; height: 37px; padding: 0 11px; border: 1px solid #e3e9df; border-radius: 8px; background: #fafcf8; color: #8d9d86; }
.kb-filter-input > .app-icon { width: 14px; height: 14px; }
.kb-filter-input:focus-within { border-color: #8fac7f; box-shadow: 0 0 0 3px #88a46c14; }
.kb-filter-input input { width: 100%; min-width: 0; padding: 8px; border: 0; outline: 0; background: transparent; color: #435d40; font: inherit; font-size: 12px; }
.kb-filter-input input:disabled { opacity: .75; }
.kb-filter-input input::placeholder { color: #8b9786; }
.kb-filter-input button { padding: 0; border: 0; background: transparent; color: #7c8f72; }
.kb-filter-input button .app-icon { width: 13px; height: 13px; }
.knowledge-page select { max-width: 100%; padding: 8px 10px; border: 1px solid #e1e8dc; border-radius: 8px; background: #fafcf7; color: #698062; font: inherit; font-size: 11px; }
.knowledge-page select:disabled { color: #8a9784; opacity: .8; }
.kb-empty { display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 43px 22px 33px; text-align: center; }
.kb-empty-art { position: relative; width: 112px; height: 83px; margin-bottom: 20px; }
.kb-art-page { position: absolute; top: 6px; left: 37px; display: grid; place-items: center; width: 52px; height: 63px; border: 1px solid #d5e3c9; border-radius: 10px; background: #f7faf2; color: #94af78; transform: rotate(10deg); box-shadow: 0 5px 10px #657e4210; }
.kb-art-page .app-icon { width: 29px; height: 29px; }
.kb-art-back { top: 7px; left: 21px; background: #edf4e5; color: #aac08e; transform: rotate(-12deg); box-shadow: none; }
.kb-art-seal { position: absolute; right: 13px; bottom: 2px; display: grid; place-items: center; width: 28px; height: 28px; border: 3px solid #fff; border-radius: 50%; background: #7f9d5c; color: #fff; }
.kb-art-seal .app-icon { width: 15px; height: 15px; }
.kb-empty h3 { margin: 0 0 10px; font-size: 18px; letter-spacing: -.015em; }
.kb-empty > p { margin: 0; color: #6b7d65; font-size: 13px; line-height: 1.9; }
.kb-empty-actions { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; margin-top: 22px; }
.kb-empty-actions button { min-height: 38px; padding: 9px 16px; font-size: 12px; }
.kb-empty-actions .secondary-button { background: #fff; color: #66815c; }
.kb-formats { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 7px; margin-top: 17px; }
.kb-formats span { padding: 3px 6px; border: 1px solid #e5ecdf; border-radius: 4px; background: #f8faf5; color: #8d9b81; font: 9px 'Segoe UI', sans-serif; }
.kb-formats small { margin-left: 4px; color: #8b987e; font-size: 9px; }
.kb-list-footer { display: flex; align-items: flex-start; gap: 7px; padding: 14px 24px; border-top: 1px solid #edf1e8; background: #fafcf7; color: #6b7d65; font-size: 11px; line-height: 1.8; }
.kb-list-footer > .app-icon { width: 13px; height: 13px; margin-top: 2px; }
.kb-visible-count { flex-shrink: 0; margin-left: auto; color: #718866; }
.kb-document-list { margin: 0; padding: 9px 24px 14px; list-style: none; }
.kb-document { display: grid; grid-template-columns: 40px minmax(0, 1fr) 58px auto; align-items: center; gap: 14px; padding: 20px 0; border-bottom: 1px solid #edf1e9; }
.kb-document:last-child { border-bottom: 0; }
.kb-document-icon { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; width: 40px; height: 48px; border: 1px solid #e2ead9; border-radius: 8px; background: #f4f8ed; color: #91aa73; }
.kb-document-icon .app-icon { width: 20px; height: 20px; }
.kb-document-icon small { max-width: 36px; overflow: hidden; text-overflow: ellipsis; font-size: 8px; letter-spacing: .03em; white-space: nowrap; }
.kb-document-info { min-width: 0; }
.kb-document-info h3 { overflow: hidden; margin: 0 0 6px; font-size: 14px; text-overflow: ellipsis; white-space: nowrap; }
.kb-document-info p { display: flex; flex-wrap: wrap; gap: 5px; margin: 0; color: #6b7d65; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.kb-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; margin-top: 8px; }
.kb-tags > span { padding: 3px 6px; border-radius: 4px; background: #eef4e7; color: #637b4e; font-size: 10px; }
.kb-tags small { color: #75856d; font-size: 10px; }
.kb-document-count { text-align: center; }
.kb-document-count strong, .kb-document-count small { display: block; }
.kb-document-count strong { color: #648151; font-size: 17px; font-weight: 500; }
.kb-document-count small { margin-top: 5px; color: #75856d; font-size: 10px; }
.kb-document-actions { display: flex; align-items: center; gap: 7px; }
.kb-view-document { padding: 6px 0 6px 5px; border: 0; background: transparent; color: #5b7847; font-size: 11px; white-space: nowrap; }
.kb-view-document:hover { color: #355a2c; text-decoration: underline; text-underline-offset: 3px; }
.kb-document-actions button .app-icon { width: 13px; height: 13px; }
.kb-delete { width: 36px; height: 36px; padding: 0; border: 1px solid transparent; border-radius: 7px; background: transparent; color: #7d8c70; }
.kb-delete:hover:not(:disabled) { border-color: #efdbd1; background: #fff6f0; color: #a85d49; }
.kb-search-sidebar { display: grid; gap: 18px; }
.kb-search-card { padding: 23px; }
.kb-search-symbol { display: grid; place-items: center; width: 37px; height: 37px; margin-bottom: 17px; border: 1px solid #e0e8d7; border-radius: 10px; background: #f0f5e9; color: #8ca571; }
.kb-search-symbol .app-icon { width: 19px; height: 19px; }
.kb-search-card .eyebrow { margin: 0 0 7px; color: #8b9a7d; font-size: 8px; letter-spacing: .13em; }
.kb-search-description { margin: 9px 0 0; color: #6b7d65; font-size: 12px; line-height: 1.8; }
#kb-query { min-height: 128px; margin: 20px 0 0; padding: 13px 14px; border-color: #e1e9d9; border-radius: 9px; background: #fafcf7; color: #546c42; font-size: 13px; line-height: 1.85; }
#kb-query::placeholder { color: #95a087; opacity: 1; }
#kb-query:focus { border-color: #91aa7c; box-shadow: 0 0 0 3px #8aa46c15; }
.kb-search-actions { display: flex; gap: 9px; margin-top: 11px; }
.kb-search-actions select { flex: 1; min-width: 0; }
.kb-search-actions .primary-button { min-height: 37px; padding: 8px 12px; font-size: 12px; gap: 6px; }
.kb-search-actions .primary-button .app-icon { width: 13px; height: 13px; }
.kb-search-shortcut { margin: 7px 0 0; color: #78886e; font-size: 10px; text-align: right; }
.kb-search-error { margin: 14px 0 0; }
.kb-query-summary { display: flex; flex-wrap: wrap; align-items: baseline; gap: 7px; margin: 0; padding: 18px 24px 0; color: #708165; font-size: 12px; line-height: 1.8; overflow-wrap: anywhere; }
.kb-query-summary strong { color: #4f6e3d; font-weight: 600; }
.kb-query-summary > span { padding: 2px 7px; border-radius: 5px; background: #eff5e8; color: #6c8456; font-size: 10px; }
.kb-results-view { scroll-margin-top: 20px; }
.kb-results-view:focus-visible { outline: 2px solid #a1b98c; outline-offset: -2px; }
.kb-search-note { display: flex; align-items: flex-start; gap: 6px; margin: 17px 0 0; padding-top: 13px; border-top: 1px solid #edf1e6; color: #6b7d65; font-size: 11px; line-height: 1.8; }
.kb-search-note .app-icon { width: 13px; height: 13px; margin-top: 2px; }
.kb-guide { padding: 21px 23px; border: 1px solid #e1e9d9; border-radius: 13px; background: #f0f5eb; }
.kb-guide h3 { display: flex; align-items: center; gap: 7px; margin: 0 0 14px; color: #587348; font-size: 13px; }
.kb-guide h3 .app-icon { width: 14px; height: 14px; }
.kb-guide ul { display: grid; gap: 10px; margin: 0; padding: 0; list-style: none; }
.kb-guide li { display: flex; align-items: flex-start; gap: 6px; color: #6b7d65; font-size: 11px; line-height: 1.7; }
.kb-guide li .app-icon { width: 12px; height: 12px; margin-top: 2px; color: #8eaa74; }
.kb-help { margin: 9px 0; color: #6b7d65; font-size: 12px; line-height: 1.8; }
.kb-results-caption { margin: 0; padding: 18px 24px 2px; color: #8a987d; font-size: 11px; line-height: 1.8; }
.kb-hit { margin: 0 24px; padding: 22px 0; border-bottom: 1px solid #edf1e5; }
.kb-hit:last-child { border: 0; }
.kb-hit-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.kb-hit-heading h3 { flex: 1; min-width: 100px; margin: 0; font-size: 14px; line-height: 1.8; }
.kb-hit-number { color: #9baa87; font: 11px Consolas, monospace; }
.kb-match-badge { padding: 4px 8px; border-radius: 20px; background: #eff5e8; color: #85996e; font-size: 9px; }
.kb-hit blockquote { margin: 12px 0; padding: 14px 16px; border-left: 2px solid #b7cd9b; border-radius: 0 8px 8px 0; background: #f7faf2; color: #526b42; font-size: 13px; line-height: 1.9; overflow-wrap: anywhere; white-space: pre-wrap; }
.kb-result-link { padding: 3px 0; border: 0; background: transparent; color: #759357; font-size: 11px; }
.kb-result-link:hover { color: #3b642f; text-decoration: underline; text-underline-offset: 4px; }
.kb-result-link .app-icon { width: 13px; height: 13px; }
.kb-filter-empty { padding: 65px 24px; text-align: center; }
.kb-filter-empty > .app-icon { width: 32px; height: 32px; margin-bottom: 16px; color: #a6bb8f; }
.kb-filter-empty h3 { margin: 0 0 10px; font-size: 17px; }
.kb-filter-empty p { margin: 0 0 20px; color: #6b7d65; font-size: 13px; line-height: 1.9; }
.kb-empty-search-icon { display: grid; place-items: center; width: 58px; height: 58px; margin: 0 auto 20px; border: 1px solid #e4ecd9; border-radius: 17px; background: #f4f8ed; color: #a1b786; }
.kb-empty-search-icon .app-icon { width: 26px; height: 26px; }
.kb-list-loading { padding: 24px; color: #8c9b7f; font-size: 12px; }
.kb-skeleton-row { display: flex; align-items: center; gap: 16px; margin-top: 25px; }
.kb-skeleton-row > i { width: 40px; height: 48px; border-radius: 8px; background: #f0f4e9; }
.kb-skeleton-row > div { display: grid; flex: 1; gap: 11px; }
.kb-skeleton-row span { width: 60%; height: 10px; border-radius: 5px; background: #f1f5ec; }
.kb-skeleton-row span + span { width: 40%; height: 7px; }
.kb-source { margin-top: 24px; padding: 25px; scroll-margin-top: 24px; }
.kb-source-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; margin-bottom: 15px; }
.kb-source-heading > div { min-width: 0; }
.kb-source-heading h2 { overflow-wrap: anywhere; line-height: 1.6; }
.kb-source-heading .eyebrow { margin-bottom: 7px; }
.kb-source-heading button { flex-shrink: 0; }
.kb-source .kb-help a { color: #729153; text-underline-offset: 3px; }
.kb-provenance { margin: 14px 0; padding: 10px 13px; border: 1px solid #e6eddd; border-radius: 8px; background: #fafcf6; color: #8c9b77; font-size: 11px; }
.kb-provenance summary { cursor: pointer; }
.kb-hash { margin: 12px 0 2px; overflow-wrap: anywhere; font: 10px Consolas, monospace; line-height: 1.7; }
.kb-page-picker { color: #78935c; font-size: 12px; }
.kb-page-picker select { margin-left: 10px; }
.kb-original { max-height: 560px; overflow: auto; margin-bottom: 0; padding: 22px; border: 1px solid #e8eedd; border-radius: 10px; background: #f8faf3; color: #60774c; font: inherit; font-size: 13px; line-height: 2.1; white-space: pre-wrap; overflow-wrap: anywhere; }
.kb-original mark { padding-block: 2px; background: #e3edbb; color: #435d2e; }
.kb-notice { display: flex; align-items: flex-start; gap: 7px; padding: 12px 16px; border: 1px solid #d7e8ca; border-radius: 9px; background: #f1f8ea; color: #64824c; font-size: 12px; line-height: 1.8; }
.kb-notice .app-icon { width: 15px; height: 15px; margin-top: 3px; }
.kb-footnote { display: flex; align-items: flex-start; gap: 7px; margin-top: 21px; padding-inline: 2px; color: #6b7d65; font-size: 11px; line-height: 1.9; }
.kb-footnote .app-icon { width: 13px; height: 13px; margin-top: 3px; }
.kb-footnote p { margin: 0; }
.kb-import-dialog { width: min(580px, calc(100% - 36px)); max-height: calc(100dvh - 48px); overflow: auto; overscroll-behavior: contain; padding: 26px; border: 1px solid #dfe8d6; border-radius: 18px; background: #fff; color: #354f37; box-shadow: 0 24px 90px #142d2630; }
.kb-import-dialog::backdrop { background: #183c2c55; backdrop-filter: blur(4px); }
.kb-modal-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 15px; margin-bottom: 23px; }
.kb-modal-heading .eyebrow { margin: 0 0 8px; color: #819b6c; font-size: 9px; }
.kb-modal-heading h2 { font-size: 22px; }
.kb-modal-heading p:last-child { margin: 9px 0 0; color: #6b7d65; font-size: 13px; line-height: 1.8; }
.kb-dialog-close { width: 36px; height: 36px; flex-shrink: 0; padding: 0; border: 1px solid #e4ebdc; border-radius: 8px; background: #f9fcf5; color: #718862; }
.kb-dialog-close:hover:not(:disabled) { background: #eff6e4; }
.kb-import fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.kb-import-tabs { display: flex; gap: 5px; margin-bottom: 18px; padding: 5px; border: 1px solid #e5edda; border-radius: 9px; background: #f6f9f0; }
.kb-import-tabs button { flex: 1; padding: 9px; border: 1px solid transparent; border-radius: 6px; background: transparent; color: #6b7d65; font: inherit; font-size: 13px; }
.kb-import-tabs button.active { border-color: #dce7cf; background: #fff; color: #6b8950; font-weight: 600; box-shadow: 0 1px 4px #476d2210; }
.kb-import label { display: block; margin: 17px 0 8px; color: #526b42; font-size: 13px; }
.kb-import label > span:not(.kb-upload-icon) { margin-left: 5px; color: #75866b; font-size: 10px; }
.kb-import input:not([type='file']), .kb-import textarea { width: 100%; max-width: 100%; min-height: 0; margin: 0; padding: 11px 12px; border: 1px solid #e0e9d5; border-radius: 8px; background: #fcfdf9; color: #647e4b; font: inherit; font-size: 13px; }
.kb-import textarea { line-height: 1.9; resize: vertical; }
.kb-import input:focus-visible, .kb-import textarea:focus-visible { outline: 2px solid #96b57a; outline-offset: 2px; }
.kb-import input::placeholder, .kb-import textarea::placeholder { color: #849674; }
.kb-import .kb-upload { display: flex; flex-direction: column; align-items: center; gap: 8px; margin: 0; padding: 25px 18px; border: 1px dashed #cadbb5; border-radius: 11px; background: #f8fbf3; text-align: center; cursor: pointer; transition: background .18s, border-color .18s; }
.kb-upload:hover, .kb-upload:focus-within, .kb-upload.is-dragging { border-color: #93b269; background: #f0f7e6; }
.kb-import fieldset:disabled .kb-upload { opacity: .65; cursor: not-allowed; }
.kb-upload-icon { display: grid; place-items: center; width: 38px; height: 38px; margin-bottom: 3px; border: 1px solid #dbe8c8; border-radius: 10px; background: #fff; color: #a2ba7e; }
.kb-upload-icon .app-icon { width: 21px; height: 21px; }
.kb-upload strong { max-width: 100%; color: #5b7b40; font-size: 14px; font-weight: 600; overflow-wrap: anywhere; }
.kb-import .kb-upload > span:not(.kb-upload-icon) { margin: 0; color: #6b7d65; font-size: 12px; }
.kb-upload small { color: #819272; font-size: 11px; }
.kb-file-card { overflow: hidden; border: 1px solid #b9d0bf; border-radius: 11px; background: #fff; box-shadow: 0 3px 10px #305a3a08; }
.kb-file-state { display: flex; align-items: flex-start; gap: 9px; padding: 13px 15px; border-bottom: 1px solid #d6e6d9; background: #eaf4ed; }
.kb-file-state-icon { display: grid; place-items: center; width: 23px; height: 23px; flex: 0 0 auto; border-radius: 50%; background: #2e7350; color: #fff; }
.kb-file-state-icon .app-icon { width: 15px; height: 15px; }
.kb-file-state > div { flex: 1; min-width: 0; }
.kb-file-state strong { color: #2c5c40; font-size: 13px; font-weight: 600; line-height: 1.7; }
.kb-file-state p { margin: 3px 0 0; color: #5f7765; font-size: 11px; line-height: 1.8; }
.kb-file-state-badge { flex-shrink: 0; padding: 4px 8px; border: 1px solid #b9d3c1; border-radius: 6px; background: #fff; color: #396b4c; font-size: 10px; font-weight: 600; white-space: nowrap; }
.kb-file-summary { display: flex; align-items: center; gap: 17px; padding: 21px 18px; }
.kb-file-summary > div { flex: 1; min-width: 0; }
.kb-file-summary strong { display: block; color: #304b3b; font-size: 14px; font-weight: 600; line-height: 1.7; overflow-wrap: anywhere; }
.kb-file-summary p { margin: 6px 0 0; color: #687e6d; font-size: 12px; line-height: 1.7; }
.kb-file-summary p > span { margin-inline: 8px; color: #a3b2a7; }
.kb-file-actions { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 15px; border-top: 1px solid #e7eee8; background: #fafcfb; }
.kb-file-actions .secondary-button { min-height: 34px; padding: 7px 10px; font-size: 11px; }
.kb-file-actions button .app-icon { width: 14px; height: 14px; }
.kb-remove-file { min-height: 34px; padding: 7px 5px; border: 0; border-radius: 5px; background: transparent; color: #866458; font: inherit; font-size: 11px; }
.kb-remove-file:hover:not(:disabled) { background: #fff0ea; color: #a45743; }
.kb-file-card.is-rejected { border-color: #e5c8b7; }
.kb-file-card.is-rejected .kb-file-state { border-color: #efdbce; background: #fff4ed; }
.kb-file-card.is-rejected .kb-file-state-icon { background: #b07543; }
.kb-file-card.is-rejected .kb-file-state strong { color: #8b5431; }
.kb-file-card.is-rejected .kb-file-state p { color: #87674d; }
.kb-file-card.is-rejected .kb-file-state-badge { border-color: #e6c4ac; color: #98643e; }
.kb-file-card.is-saving .kb-file-state { background: #edf4f8; border-color: #d8e4eb; }
.kb-file-card.is-saving .kb-file-state-icon { background: #567a91; }
.kb-file-picker.is-dragging :is(.kb-file-card, .kb-upload) { border-color: #739c77; background: #f0f7e6; box-shadow: 0 0 0 3px #739c771a; }
.kb-file-picker:has(> input:focus-visible) > :is(.kb-upload, .kb-file-card) { outline: 2px solid #80a081; outline-offset: 3px; }
.kb-document-icon { padding: 0; border: 0; background: transparent; }
.kb-optional-fields { margin-top: 20px; padding: 15px 0; border-top: 1px solid #e9efdf; border-bottom: 1px solid #e9efdf; }
.kb-optional-fields summary { display: flex; align-items: center; gap: 9px; list-style: none; color: #5b7847; font-size: 13px; cursor: pointer; }
.kb-optional-fields summary::-webkit-details-marker { display: none; }
.kb-optional-fields summary > span { color: #acb898; font-size: 10px; }
.kb-optional-fields summary > .app-icon { width: 13px; height: 13px; margin-left: auto; transition: transform .18s; }
.kb-optional-fields[open] summary > .app-icon { transform: rotate(90deg); }
.kb-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.kb-fields > div { min-width: 0; }
.kb-import-privacy { display: flex; align-items: flex-start; gap: 7px; margin: 16px 0 0; color: #6b7d65; font-size: 11px; line-height: 1.9; }
.kb-import-privacy .app-icon { width: 13px; height: 13px; margin-top: 2px; }
.kb-modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 21px; }
.kb-modal-actions .primary-button { min-height: 39px; padding: 10px 16px; font-size: 12px; }
.kb-modal-actions .secondary-button { min-width: 70px; }
.kb-spinning { animation: kb-spin 1.2s linear infinite; }
@keyframes kb-spin { to { transform: rotate(360deg); } }
@media (max-width: 1000px) {
  .kb-workspace { grid-template-columns: minmax(0, 1fr) 290px; gap: 18px; }
  .kb-search-card { padding: 20px; }
  .kb-library-heading, .kb-list-filters { padding-inline: 20px; }
  .kb-view-tabs { padding-inline: 20px; }
  .kb-document-list { padding-inline: 20px; }
  .kb-document { grid-template-columns: 36px minmax(0, 1fr) auto; gap: 11px; }
  .kb-document-count { display: none; }
  .kb-stat-card { padding: 18px; }
  .kb-stat-card .kb-mode { font-size: 20px; }
}
@media (max-width: 800px) {
  .kb-workspace { grid-template-columns: minmax(0, 1fr); gap: 20px; }
  .kb-search-sidebar { grid-template-columns: minmax(0, 1fr) minmax(0, .85fr); align-items: start; }
  .kb-search-symbol { display: none; }
  .kb-overview { gap: 12px; }
  .kb-stat-card { padding: 16px; }
  .kb-stat-card .kb-mode { font-size: 18px; margin-top: 14px; }
  .kb-ready-badge { display: none; }
  .kb-heading-actions .secondary-button { display: none; }
  .kb-document { grid-template-columns: 40px minmax(0, 1fr) 58px auto; }
  .kb-document-count { display: block; }
}
@media (max-width: 560px) {
  .kb-page-heading { align-items: flex-start; flex-direction: column; gap: 17px; margin-bottom: 22px; }
  .kb-page-heading h1 { font-size: 30px; margin-bottom: 9px; }
  .kb-page-description { font-size: 12px; }
  .kb-heading-actions { width: 100%; }
  .kb-heading-actions .primary-button { flex: 1; }
  .kb-heading-actions .secondary-button { display: inline-flex; }
  .kb-overview { grid-template-columns: 1fr 1fr; gap: 10px; }
  .kb-stat-card { padding: 14px 16px; border-radius: 11px; }
  .kb-stat-top { font-size: 11px; }
  .kb-stat-icon { width: 25px; height: 25px; }
  .kb-stat-icon .app-icon { width: 14px; height: 14px; }
  .kb-stat-card strong { font-size: 26px; margin-top: 6px; }
  .kb-stat-card p { font-size: 10px; }
  .kb-engine-card { grid-column: span 2; }
  .kb-engine-card .kb-stat-top { float: left; display: block; }
  .kb-engine-card .kb-stat-icon { display: none; }
  .kb-stat-card .kb-mode { justify-content: flex-end; margin: 0; font-size: 18px; }
  .kb-engine-card p { clear: both; margin-top: 9px; }
  .kb-ready-badge { display: inline-flex; }
  .kb-engine-note { margin-bottom: 21px; font-size: 10px; }
  .kb-engine-note .text-button { margin-left: 19px; }
  .kb-library-heading { padding: 20px 18px 16px; gap: 10px; }
  .kb-library-heading h2 { font-size: 17px; }
  .kb-library-heading p { font-size: 11px; }
  .kb-view-tabs { padding-inline: 18px; gap: 22px; }
  .kb-view-tabs button { font-size: 12px; }
  .kb-list-filters { padding: 16px 18px 0; gap: 7px; }
  .kb-filter-input { padding-inline: 8px; }
  .kb-filter-input input { padding-inline: 6px; font-size: 11px; }
  .kb-list-filters select { max-width: 100px; padding: 8px 6px; font-size: 10px; }
  .kb-empty { padding: 35px 18px 26px; }
  .kb-empty h3 { font-size: 16px; }
  .kb-empty > p { font-size: 12px; }
  .kb-empty-art { margin-bottom: 15px; }
  .kb-empty-actions { gap: 8px; }
  .kb-empty-actions button { padding: 9px 12px; font-size: 11px; }
  .kb-formats { gap: 5px; }
  .kb-formats small { width: 100%; margin: 3px 0 0; }
  .kb-list-footer { padding: 13px 18px; font-size: 10px; }
  .kb-visible-count { display: none; }
  .kb-document-list { padding: 6px 18px; }
  .kb-document { grid-template-columns: 36px minmax(0, 1fr); gap: 10px; padding: 18px 0; }
  .kb-document-icon { width: 36px; }
  .kb-document-info h3 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; font-size: 13px; line-height: 1.7; white-space: normal; }
  .kb-document-info p { font-size: 11px; }
  .kb-document-count { display: none; }
  .kb-document-actions { grid-column: 2; justify-content: flex-start; gap: 15px; margin-top: -2px; }
  .kb-view-document { padding-left: 0; }
  .kb-search-sidebar { grid-template-columns: 1fr; gap: 16px; }
  .kb-search-card { padding: 22px; }
  .kb-guide { padding: 20px 22px; }
  .kb-filter-empty { padding: 50px 20px; }
  .kb-hit { margin-inline: 18px; padding-block: 18px; }
  .kb-hit-heading { align-items: flex-start; }
  .kb-match-badge { margin-left: 24px; }
  .kb-results-caption { padding-inline: 18px; font-size: 10px; }
  .kb-query-summary { padding-inline: 18px; font-size: 11px; }
  .kb-source { padding: 20px 18px; }
  .kb-source-heading { flex-direction: column; gap: 14px; }
  .kb-source-heading h2 { font-size: 17px; }
  .kb-original { padding: 15px; font-size: 12px; }
  .kb-import-dialog { width: calc(100% - 24px); max-height: calc(100dvh - 24px); padding: 22px 19px; border-radius: 14px; }
  .kb-modal-heading h2 { font-size: 20px; }
  .kb-modal-heading p:last-child { font-size: 12px; }
  .kb-import .kb-upload { padding: 24px 13px; }
  .kb-upload strong { font-size: 13px; }
  .kb-import .kb-upload > span:not(.kb-upload-icon) { font-size: 11px; }
  .kb-upload small { font-size: 10px; }
  .kb-file-state { padding: 12px; gap: 8px; }
  .kb-file-state strong { font-size: 12px; }
  .kb-file-state p { font-size: 10px; }
  .kb-file-state-badge { padding: 3px 6px; font-size: 9px; }
  .kb-file-summary { padding: 18px 14px; gap: 13px; }
  .kb-file-summary strong { font-size: 13px; }
  .kb-file-summary p { font-size: 11px; }
  .kb-file-actions { padding: 10px 12px; }
  .kb-optional-fields summary { gap: 7px; font-size: 12px; }
  .kb-optional-fields summary > span { font-size: 9px; }
  .kb-fields { grid-template-columns: 1fr; gap: 0; }
  .kb-modal-actions button { flex: 1; }
  .kb-footnote { font-size: 10px; }
}
@media (prefers-reduced-motion: reduce) { .kb-spinning { animation: none; } }
@media (pointer: coarse) {
  .kb-delete, .kb-dialog-close { width: 44px; height: 44px; }
  .kb-filter-input { height: 44px; }
  .kb-filter-input button { min-width: 32px; min-height: 32px; }
}
@media print { .kb-import-dialog { display: none !important; } }
</style>
