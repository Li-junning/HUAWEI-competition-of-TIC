<script setup lang="ts">
import { computed } from 'vue'
import { filePresentation } from '../utils/fileType'

const props = withDefaults(defineProps<{ filename?: string | null; size?: 'small' | 'large' }>(), { filename: null, size: 'small' })
const format = computed(() => filePresentation(props.filename))
</script>

<template>
  <span class="file-type-icon" :class="['file-type-' + format.kind, 'file-type-' + size]" aria-hidden="true">
    <svg class="file-type-paper" viewBox="0 0 52 64" fill="none" focusable="false">
      <path d="M10 3h22l13 13v42a3 3 0 0 1-3 3H10a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3Z" fill="var(--file-paper)" stroke="var(--file-outline)" stroke-width="1.5" stroke-linejoin="round" />
      <path d="M32 3v10a3 3 0 0 0 3 3h10" fill="var(--file-fold)" stroke="var(--file-outline)" stroke-width="1.5" stroke-linejoin="round" />
      <path d="M15 25h22M15 32h16" stroke="var(--file-outline)" stroke-width="2" stroke-linecap="round" />
    </svg>
    <span class="file-type-badge">{{ format.badge }}</span>
  </span>
</template>

<style scoped>
.file-type-icon { --file-color: #637568; --file-paper: #f4f7f4; --file-outline: #b5c3b8; --file-fold: #e5ece5; position: relative; display: inline-block; width: 36px; height: 44px; flex: 0 0 auto; vertical-align: middle; }
.file-type-paper { display: block; width: 100%; height: 100%; }
.file-type-badge { position: absolute; right: 3px; bottom: 6px; left: 0; display: flex; align-items: center; justify-content: center; height: 15px; border: 1px solid #ffffffbb; border-radius: 3px; background: var(--file-color); color: #fff; font: 700 8px 'Segoe UI', sans-serif; letter-spacing: .02em; box-shadow: 0 2px 3px #182c2710; }
.file-type-pdf { --file-color: #c84e48; --file-paper: #fff5f3; --file-outline: #e5b0aa; --file-fold: #f9ded9; }
.file-type-word { --file-color: #2c68b4; --file-paper: #f1f6fe; --file-outline: #9ab9e2; --file-fold: #d9e7fa; }
.file-type-word .file-type-badge { right: 11px; left: -1px; font-size: 11px; }
.file-type-markdown { --file-color: #735aa6; --file-paper: #f8f5fc; --file-outline: #c6b8dd; --file-fold: #e9e0f5; }
.file-type-text { --file-color: #53826a; --file-paper: #f2f8f3; --file-outline: #accbb6; --file-fold: #dcece0; }
.file-type-large { width: 56px; height: 68px; }
.file-type-large .file-type-badge { right: 4px; bottom: 10px; height: 23px; border-radius: 4px; font-size: 12px; }
.file-type-word.file-type-large .file-type-badge { right: 18px; left: -2px; height: 29px; font-size: 20px; }
</style>
