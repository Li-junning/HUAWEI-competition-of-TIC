<script setup lang="ts">
defineProps<{ name: 'shield' | 'check' | 'search' | 'file' | 'library' | 'arrow' | 'sparkles' | 'close' | 'info' | 'layers' | 'plus' | 'upload' | 'refresh' | 'trash' | 'chevron' }>()

const paths = {
  shield: ['M12 3 4.5 6v5c0 4.5 3.2 7.9 7.5 10 4.3-2.1 7.5-5.5 7.5-10V6L12 3Z', 'm8.5 11.5 2.5 2.5 4.5-5'],
  check: ['m5 12 4 4L19 6'],
  search: ['M20 20 16 16', 'M18 10.5a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z'],
  file: ['M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z', 'M14 3v6h6', 'M8 13h8', 'M8 17h5'],
  library: ['M4 4h4v16H4z', 'M10 4h4v16h-4z', 'm17 4 4 1-3 15-4-1 3-15Z'],
  arrow: ['M4 12h16', 'm14 6 6 6-6 6'],
  sparkles: ['m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z'],
  close: ['m6 6 12 12', 'M18 6 6 18'],
  info: ['M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z', 'M12 11v6', 'M12 7h.01'],
  layers: ['m12 3 9 5-9 5-9-5 9-5Z', 'm3 12 9 5 9-5', 'm3 16 9 5 9-5'],
  plus: ['M12 5v14', 'M5 12h14'],
  upload: ['M12 16V3', 'm7 8 5-5 5 5', 'M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4'],
  refresh: ['M20 7v5h-5', 'M4 17v-5h5', 'M6.1 6.1A8 8 0 0 1 19.5 10', 'M4.5 14a8 8 0 0 0 13.4 3.9'],
  trash: ['M3 6h18', 'M9 6V3h6v3', 'm5 6 1 15h12l1-15', 'M10 10v7', 'M14 10v7'],
  chevron: ['m9 5 7 7-7 7'],
}
</script>

<template>
  <svg class="app-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">
    <path v-for="(d, index) in paths[name]" :key="index" :d="d" />
  </svg>
</template>
