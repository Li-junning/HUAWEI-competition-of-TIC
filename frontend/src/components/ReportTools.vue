<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

defineProps<{ exportable: boolean }>()
const emit = defineEmits<{ export: [format: 'json' | 'md']; print: [] }>()
const menu = ref<HTMLDetailsElement | null>(null)
const trigger = ref<HTMLElement | null>(null)

function close(restoreFocus = false): void {
  if (menu.value) menu.value.open = false
  if (restoreFocus) trigger.value?.focus()
}
function closeOutside(event: PointerEvent): void {
  if (event.target instanceof Node && !menu.value?.contains(event.target)) close()
}
function closeOnFocusOut(event: FocusEvent): void {
  if (event.relatedTarget instanceof Node && !menu.value?.contains(event.relatedTarget)) close()
}
function exportFile(format: 'json' | 'md'): void { close(true); emit('export', format) }
function print(): void { close(true); emit('print') }
onMounted(() => document.addEventListener('pointerdown', closeOutside))
onUnmounted(() => document.removeEventListener('pointerdown', closeOutside))
</script>

<template>
  <details ref="menu" class="report-tools" @keydown.esc.prevent.stop="close(true)" @focusout="closeOnFocusOut">
    <summary ref="trigger" class="secondary-button">报告工具<AppIcon name="chevron" /></summary>
    <div class="report-tools-options" role="group" aria-label="打印与导出报告">
      <template v-if="exportable"><button type="button" @click="exportFile('md')"><AppIcon name="file" /><span>导出 Markdown<small>便于阅读和分享</small></span></button><button type="button" @click="exportFile('json')"><AppIcon name="layers" /><span>导出 JSON<small>保留完整核验数据</small></span></button></template>
      <button type="button" @click="print"><AppIcon name="file" /><span>打印报告<small>打印或另存为 PDF</small></span></button>
    </div>
  </details>
</template>

<style scoped>
.report-tools { position: relative; }
.report-tools summary { display: flex; align-items: center; justify-content: center; gap: 9px; list-style: none; cursor: pointer; }
.report-tools summary::-webkit-details-marker { display: none; }
.report-tools summary > .app-icon { width: 13px; height: 13px; transform: rotate(90deg); transition: transform .15s; }
.report-tools[open] summary > .app-icon { transform: rotate(270deg); }
.report-tools[open] summary { border-color: #9db69b; background: #f1f6ef; }
.report-tools-options { position: absolute; z-index: 10; top: calc(100% + 7px); right: 0; width: 218px; max-width: calc(100vw - 40px); padding: 6px; border: 1px solid #dce6d7; border-radius: 11px; background: #fff; box-shadow: 0 9px 30px #25472b17; }
.report-tools-options button { display: flex; align-items: center; gap: 11px; width: 100%; padding: 11px 12px; border: 0; border-radius: 7px; background: transparent; color: #486442; font: inherit; font-size: 12px; text-align: left; }
.report-tools-options button:hover { background: #f2f7ed; }
.report-tools-options button .app-icon { width: 18px; height: 18px; color: #7d9770; }
.report-tools-options small { display: block; margin-top: 4px; color: #73856b; font-size: 10px; line-height: 1.6; }
@media print { .report-tools { display: none !important; } }
</style>
