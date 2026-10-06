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
const sourceError = ref('')
const sourcePanel = ref<HTMLElement | null>(null)
const sourceContent = ref<HTMLElement | null>(null)
const sourceHighlight = ref<HTMLElement | null>(null)
const libraryPanel = ref<HTMLElement | null>(null)
const activeDocumentId = ref<string | null>(null)
const activeChunkId = ref<string | null>(null)
const sourceHit = ref<KnowledgeHit | null>(null)
let sourceTrigger: HTMLElement | null = null
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
function resetSourceScroll(): void {
  if (!sourceContent.value) return
  sourceContent.value.scrollTop = 0
  if (sourceHighlight.value) {
    sourceContent.value.scrollTop = Math.max(0, sourceHighlight.value.getBoundingClientRect().top - sourceContent.value.getBoundingClientRect().top - 80)
  }
}
async function changePage(nextPage: number): Promise<void> {
  if (!selected.value || nextPage < 1 || nextPage > selected.value.pages.length) return
  page.value = nextPage
  selection.value = null
  await nextTick()
  resetSourceScroll()
}
async function closeSource(): Promise<void> {
  documentRequest += 1
  selected.value = null; sourceLoading.value = false; sourceError.value = ''
  activeDocumentId.value = null; activeChunkId.value = null; selection.value = null
  sourceHit.value = null
  await nextTick()
  const target = sourceTrigger?.isConnected ? sourceTrigger : libraryPanel.value
  target?.focus({ preventScroll: true })
  if (window.matchMedia('(max-width: 800px)').matches) {
    target?.scrollIntoView({ block: 'nearest', behavior: 'auto' })
  }
  sourceTrigger = null
}
async function viewDocument(id: string, hit?: KnowledgeHit, event?: Event): Promise<void> {
  const request = ++documentRequest
  if (event?.currentTarget instanceof HTMLElement) sourceTrigger = event.currentTarget
  activeDocumentId.value = id
  activeChunkId.value = hit?.chunk_id ?? null
  sourceHit.value = hit ?? null
  sourceLoading.value = true; sourceError.value = ''; selected.value = null
  selection.value = null
  try {
    const result = await getKnowledgeDocument(id)
    if (disposed || request !== documentRequest) return
    selected.value = result
    const hitPage = hit?.page ?? 1
    page.value = Number.isInteger(hitPage) && hitPage >= 1 && hitPage <= result.pages.length ? hitPage : 1
    if (hit) selection.value = { start: hit.char_start, end: hit.char_end }
    else if (id === props.initialDocument) {
      const params = new URLSearchParams(window.location.search)
      const requestedPage = Number(params.get('kb_page') ?? 1)
      page.value = Number.isInteger(requestedPage) && requestedPage >= 1 && requestedPage <= result.pages.length ? requestedPage : 1
      if (params.has('kb_start') && params.has('kb_end')) selection.value = { start: Number(params.get('kb_start')), end: Number(params.get('kb_end')) }
    }
  } catch (reason) { if (!disposed && request === documentRequest) sourceError.value = message(reason) }
  finally { if (!disposed && request === documentRequest) sourceLoading.value = false }
  await nextTick()
  if (disposed || request !== documentRequest) return
  resetSourceScroll()
  if (window.matchMedia('(max-width: 800px)').matches) {
    sourcePanel.value?.focus({ preventScroll: true })
    sourcePanel.value?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'start' })
  }
}
async function remove(doc: KnowledgeDocument): Promise<void> {
  if (busy.value || searching.value || !window.confirm(`删除“${doc.title}”？后续核验将不再检索它；已有报告中的证据片段会保留。`)) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await deleteKnowledgeDocument(doc.document_id)
    if (disposed) return
    if (activeDocumentId.value === doc.document_id) { sourceTrigger = null; await closeSource() }
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
      <div>
        <p class="eyebrow">YOUR EVIDENCE LIBRARY</p>
        <h1 id="knowledge-title">我的知识库</h1>
        <p class="kb-page-description">保存参考资料，检索证据，随时对照原文。</p>
      </div>
      <div class="kb-heading-actions">
        <button type="button" class="secondary-button" @click="$emit('back')"><AppIcon name="arrow" class="kb-back-arrow" />返回核验</button>
        <button ref="importTrigger" type="button" class="primary-button" :disabled="busy || searching" @click="openImport()"><AppIcon name="plus" />添加资料</button>
      </div>
    </header>

    <div class="kb-overview" aria-label="知识库概况">
      <div class="kb-metrics">
        <span><AppIcon name="library" /><strong>{{ status ? status.document_count.toLocaleString() : '—' }}</strong>份资料</span>
        <span><AppIcon name="layers" /><strong>{{ status ? status.chunk_count.toLocaleString() : '—' }}</strong>个证据片段</span>
        <span class="kb-local-badge"><AppIcon name="shield" />本机保存</span>
      </div>
      <details v-if="status" class="kb-engine-details">
        <summary><i :class="{ ready: status.semantic_ready }" aria-hidden="true"></i>{{ status.mode === 'hybrid' ? '混合检索' : '关键词检索' }}<AppIcon name="chevron" /></summary>
        <div class="kb-engine-popover">
          <strong>检索状态</strong><p>{{ status.message }}</p>
          <p>已建立 {{ status.indexed_chunks.toLocaleString() }} / {{ status.chunk_count.toLocaleString() }} 个语义索引</p>
          <button v-if="status.semantic_ready && status.indexed_chunks < status.chunk_count" class="text-button" type="button" :disabled="busy || searching" @click="reindex">{{ busy ? '正在处理…' : '补建语义索引' }}<AppIcon name="arrow" /></button>
        </div>
      </details>
      <span v-else class="kb-status-loading">正在读取状态…</span>
    </div>

    <p v-if="error" class="global-error" role="alert">{{ error }}</p>
    <p v-if="notice" class="kb-notice" role="status"><AppIcon name="check" />{{ notice }}</p>

    <section class="kb-card kb-search-card" aria-labelledby="kb-search-title">
      <div class="kb-search-heading"><span class="kb-search-symbol"><AppIcon name="search" /></span><div><h2 id="kb-search-title">在资料中查找证据</h2><p>输入问题或待核验的说法，找到相关的原文片段。</p></div></div>
      <form @submit.prevent="search">
        <div class="kb-query-field"><label class="sr-only" for="kb-query">检索问题或待验证声明</label><textarea id="kb-query" v-model="query" rows="2" maxlength="2000" :disabled="searching || busy" :aria-describedby="searchError ? 'kb-search-note kb-search-error' : 'kb-search-note'" placeholder="例如：这项技术有哪些适用条件？" @keydown="handleSearchKeydown" /></div>
        <div class="kb-search-actions">
          <select v-model="tag" :disabled="searching || busy || !knownTags.length" aria-label="按分类标签检索"><option value="">全部标签</option><option v-for="item in knownTags" :key="item" :value="item">{{ item }}</option></select>
          <button class="primary-button" type="submit" :disabled="searching || busy || !query.trim()"><AppIcon name="search" />{{ searching ? '检索中…' : '检索证据' }}</button>
        </div>
        <p v-if="searchError" id="kb-search-error" class="form-error kb-search-error" role="alert">{{ searchError }}</p>
      </form>
      <div class="kb-search-footer"><p id="kb-search-note"><AppIcon name="info" />匹配片段提供核验线索，命中与排序不代表说法正确。</p><span class="keyboard-shortcut">Ctrl / ⌘ + Enter</span></div>
    </section>

    <div class="kb-workspace">
      <section ref="libraryPanel" class="kb-card kb-library" tabindex="-1" aria-labelledby="kb-library-title">
        <div class="kb-library-heading"><h2 id="kb-library-title">资料与证据</h2><button type="button" class="kb-refresh-button" :disabled="loading || busy || searching" @click="error = ''; refresh()"><AppIcon name="refresh" :class="{ 'kb-spinning': loading }" />刷新</button></div>
        <div class="kb-view-tabs" role="group" aria-label="切换资料与检索结果">
          <button type="button" :class="{ active: activeView === 'documents' }" :aria-pressed="activeView === 'documents'" aria-controls="kb-documents-view" @click="activeView = 'documents'">全部资料<span>{{ documents.length }}</span></button>
          <button type="button" :class="{ active: activeView === 'results' }" :aria-pressed="activeView === 'results'" aria-controls="kb-results-view" @click="activeView = 'results'">检索结果<span v-if="searched">{{ hits.length }}</span></button>
        </div>

        <div v-if="activeView === 'documents'" id="kb-documents-view" class="kb-documents-view" :aria-busy="loading">
          <div class="kb-list-filters">
            <div class="kb-filter-input"><AppIcon name="search" /><label class="sr-only" for="kb-document-query">筛选资料标题、发布方或标签</label><input id="kb-document-query" v-model="documentQuery" :disabled="loading || !documents.length" placeholder="筛选标题、发布方或标签" /><button v-if="documentQuery" type="button" aria-label="清空资料筛选" @click="documentQuery = ''"><AppIcon name="close" /></button></div>
            <select v-model="documentTag" :disabled="loading || !knownTags.length" aria-label="筛选资料标签"><option value="">全部标签</option><option v-for="item in knownTags" :key="item" :value="item">{{ item }}</option></select>
          </div>
          <div class="kb-list-scroll">
            <div v-if="loading" class="kb-list-loading" role="status"><span>正在读取资料…</span><div v-for="index in 3" :key="index" class="kb-skeleton-row" aria-hidden="true"><i></i><div><span></span><span></span></div></div></div>
            <div v-else-if="!documents.length" class="kb-empty">
              <span class="kb-empty-icon"><AppIcon name="library" /></span><h3>添加第一份参考资料</h3><p>上传论文、白皮书或政策文件，<br />为下一次核验积累依据。</p>
              <div class="kb-empty-actions"><button type="button" class="primary-button" :disabled="busy || searching" @click="openImport('file')"><AppIcon name="upload" />上传资料</button><button type="button" class="secondary-button" :disabled="busy || searching" @click="openImport('text')">粘贴正文</button></div>
              <p class="kb-formats">Word · PDF · TXT · Markdown<br /><small>单文件最多 2 MiB</small></p>
            </div>
            <div v-else-if="!filteredDocuments.length" class="kb-filter-empty"><AppIcon name="search" /><h3>没有匹配的资料</h3><p>试试其他关键词或标签。</p><button type="button" class="secondary-button" @click="documentQuery = ''; documentTag = ''">清除筛选</button></div>
            <ul v-else class="kb-document-list" aria-label="已保存资料">
              <li v-for="doc in filteredDocuments" :key="doc.document_id" class="kb-document" :class="{ 'is-selected': activeDocumentId === doc.document_id }">
                <button type="button" class="kb-document-open" :aria-pressed="activeDocumentId === doc.document_id" aria-controls="kb-source-view" @click="viewDocument(doc.document_id, undefined, $event)">
                  <span class="kb-document-icon"><FileTypeIcon :filename="doc.filename" /><span class="sr-only">{{ documentFormat(doc) }} 资料</span></span>
                  <span class="kb-document-info"><strong :title="doc.title">{{ doc.title }}</strong><span class="kb-document-meta">{{ doc.publisher || '发布方未填写' }} · {{ doc.char_count.toLocaleString() }} 字符</span><span class="kb-document-evidence">{{ doc.chunk_count }} 个证据片段<span class="kb-document-read">{{ activeDocumentId === doc.document_id ? '阅读中' : '查看原文' }}<AppIcon name="chevron" /></span></span><span v-if="doc.tags.length" class="kb-tags"><span v-for="item in doc.tags.slice(0, 2)" :key="item" :title="item">{{ item }}</span><span v-if="doc.tags.length > 2" :title="doc.tags.slice(2).join('、')">+{{ doc.tags.length - 2 }}</span></span><span class="kb-document-date">{{ formatDate(doc.created_at) }} 导入</span></span>
                </button>
                <button type="button" class="kb-delete" :disabled="busy || searching" :aria-label="'删除资料：' + doc.title" title="删除资料" @click="remove(doc)"><AppIcon name="trash" /></button>
              </li>
            </ul>
          </div>
          <div class="kb-list-footer"><AppIcon name="shield" /><span>新建核验任务时自动检索</span><span class="kb-visible-count">{{ filteredDocuments.length }} / {{ documents.length }} 份</span></div>
        </div>

        <div v-else id="kb-results-view" ref="resultsPanel" class="kb-results-view" tabindex="-1" role="region" aria-label="知识库检索结果" :aria-busy="searching">
          <p v-if="searching" class="kb-query-summary" role="status">正在检索相关证据…</p>
          <p v-else-if="searched" class="kb-query-summary"><span :title="lastQuery">检索：<strong>{{ lastQuery }}</strong></span><span v-if="lastTag" class="kb-tag-label">{{ lastTag }}</span></p>
          <div class="kb-list-scroll">
            <div v-if="!searched" class="kb-filter-empty"><AppIcon name="search" /><h3>从一个问题开始</h3><p>在上方输入问题或说法，<br />查找证据并对照原文。</p></div>
            <div v-else-if="!hits.length" class="kb-filter-empty"><AppIcon name="search" /><h3>未找到相关证据</h3><p>换个表达，或添加更多资料。<br />未命中不能作为判定说法为假的依据。</p></div>
            <template v-else>
              <p class="kb-results-caption">找到 {{ hits.length }} 个片段 · 点击片段定位原文</p>
              <article v-for="(hit, index) in hits" :key="hit.chunk_id" class="kb-hit" :class="{ 'is-selected': activeChunkId === hit.chunk_id }">
                <button type="button" class="kb-hit-open" :aria-pressed="activeChunkId === hit.chunk_id" aria-controls="kb-source-view" @click="viewDocument(hit.document_id, hit, $event)">
                  <span class="kb-hit-heading"><span class="kb-hit-number">{{ String(index + 1).padStart(2, '0') }}</span><strong>{{ hit.title }}</strong></span>
                  <span class="kb-hit-meta">{{ hit.publisher || '发布方未填写' }}<template v-if="hit.page"> · 第 {{ hit.page }} 页</template></span>
                  <span class="kb-hit-excerpt">{{ hit.excerpt }}</span>
                  <span class="kb-hit-footer"><span class="kb-match-badge">{{ hit.matched_by.includes('semantic') ? (hit.matched_by.includes('keyword') ? '关键词 + 语义' : '语义匹配') : '关键词匹配' }}</span><span class="kb-result-link">定位原文<AppIcon name="arrow" /></span></span>
                </button>
              </article>
            </template>
          </div>
        </div>
      </section>

      <section id="kb-source-view" ref="sourcePanel" class="kb-card kb-source" :class="{ 'has-source': sourceLoading || selected || sourceError }" tabindex="-1" aria-labelledby="kb-source-title" :aria-busy="sourceLoading">
        <div class="kb-reader-bar"><h2 id="kb-source-title"><AppIcon name="file" />原文阅读</h2><button v-if="sourceLoading || selected || sourceError" type="button" class="kb-reader-close" aria-label="关闭原文" @click="closeSource"><AppIcon name="close" /></button><span v-else>选择资料后在此查看</span></div>
        <div v-if="sourceLoading" class="kb-reader-empty" role="status"><AppIcon name="refresh" class="kb-spinning" /><h3>正在读取原文…</h3><p>稍候即可查看资料正文。</p></div>
        <div v-else-if="sourceError" class="kb-reader-empty"><AppIcon name="info" /><h3>暂时无法读取原文</h3><p role="alert">{{ sourceError }}</p><button v-if="activeDocumentId" type="button" class="secondary-button" @click="viewDocument(activeDocumentId, sourceHit ?? undefined)"><AppIcon name="refresh" />重新读取</button></div>
        <template v-else-if="selected">
          <div class="kb-source-heading"><span class="kb-source-format">{{ documentFormat(selected.document) }}</span><h3 :title="selected.document.title">{{ selected.document.title }}</h3><p><span class="kb-source-publisher" :title="selected.document.publisher ?? undefined">{{ selected.document.publisher || '发布方未填写' }}</span><span class="kb-source-count">· {{ selected.document.char_count.toLocaleString() }} 字符</span></p></div>
          <div class="kb-reader-tools">
            <details :key="selected.document.document_id" class="kb-provenance"><summary>来源信息<AppIcon name="chevron" /></summary><div><p>资料标题：{{ selected.document.title }}</p><p>发布方：{{ selected.document.publisher || '未填写' }}</p><p v-if="selected.document.tags.length">分类标签：{{ selected.document.tags.join('、') }}</p><p>发布日期：{{ formatDate(selected.document.published_at) }}</p><p>导入时间：{{ formatDate(selected.document.created_at) }}</p><a v-if="safeExternalUrl(selected.document.source_url)" :href="safeExternalUrl(selected.document.source_url) ?? undefined" target="_blank" rel="noopener noreferrer">打开原始出处 ↗</a><p class="kb-hash">SHA-256：{{ selected.document.content_hash }}</p></div></details>
            <span v-if="highlight" class="kb-highlight-label"><i aria-hidden="true"></i>已定位证据片段</span>
            <div v-if="selected.pages.length > 1" class="kb-page-picker"><button type="button" aria-label="上一页原文" :disabled="page <= 1" @click="changePage(page - 1)"><AppIcon name="chevron" class="kb-prev" /></button><label class="sr-only" for="kb-source-page">原文页码</label><select id="kb-source-page" :value="page" @change="changePage(Number(($event.target as HTMLSelectElement).value))"><option v-for="(_, index) in selected.pages" :key="index" :value="index + 1">第 {{ index + 1 }} / {{ selected.pages.length }} 页</option></select><button type="button" aria-label="下一页原文" :disabled="page >= selected.pages.length" @click="changePage(page + 1)"><AppIcon name="chevron" /></button></div>
            <span v-else class="kb-single-page">第 1 / 1 页</span>
          </div>
          <div ref="sourceContent" class="kb-source-content" tabindex="0" role="region" aria-label="可滚动的资料正文"><pre class="kb-original"><template v-if="highlight">{{ currentCharacters.slice(0, highlight.start).join('') }}<mark ref="sourceHighlight">{{ currentCharacters.slice(highlight.start, highlight.end).join('') }}</mark>{{ currentCharacters.slice(highlight.end).join('') }}</template><template v-else>{{ currentText || '这一页没有提取到文字。' }}</template></pre></div>
          <div class="kb-reader-footer"><AppIcon name="info" />正文为提取文字，版式以原始文件为准。</div>
        </template>
        <div v-else class="kb-reader-empty"><span class="kb-reader-illustration" aria-hidden="true"><AppIcon name="file" /><span><AppIcon name="check" /></span></span><p class="eyebrow">READ WITH CONTEXT</p><h3>让每一条证据，都有出处</h3><p>选择左侧资料，查看完整正文；<br />点击检索片段，直接定位对应内容。</p><span class="kb-reader-tip"><AppIcon name="layers" />资料、证据、原文，在同一处对照</span></div>
      </section>
    </div>

    <footer class="kb-footnote"><AppIcon name="info" /><p>资料变更仅影响后续核验，已有报告会保留当时的证据与资料版本。</p></footer>

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

<style scoped src="./knowledge-page.css"></style>

<style scoped>
.kb-help { margin: 9px 0; color: #627366; font-size: 12px; line-height: 1.8; }
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
@media (max-width: 560px) {
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
}
@media (pointer: coarse) {
  .kb-dialog-close { width: 44px; height: 44px; }
}
@media print { .kb-import-dialog { display: none !important; } }
</style>
