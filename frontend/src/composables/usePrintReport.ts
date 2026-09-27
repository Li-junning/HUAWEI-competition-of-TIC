import { onScopeDispose } from 'vue'

/** Print all disclosure content, then restore the reader's expansion choices. */
export function usePrintReport() {
  const expanded = new Set<HTMLDetailsElement>()
  function beforePrint(): void {
    document.querySelectorAll<HTMLDetailsElement>('main details:not([open])').forEach(detail => {
      expanded.add(detail)
      detail.open = true
    })
  }
  function afterPrint(): void {
    expanded.forEach(detail => { detail.open = false })
    expanded.clear()
  }
  window.addEventListener('beforeprint', beforePrint)
  window.addEventListener('afterprint', afterPrint)
  onScopeDispose(() => {
    afterPrint()
    window.removeEventListener('beforeprint', beforePrint)
    window.removeEventListener('afterprint', afterPrint)
  })
  return { printReport: () => window.print() }
}
