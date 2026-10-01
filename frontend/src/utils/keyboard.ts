/** Submit shortcuts must not interrupt IME composition or repeat while held. */
export function isSubmitShortcut(event: KeyboardEvent): boolean {
  return event.key === 'Enter' && (event.ctrlKey || event.metaKey)
    && !event.isComposing && event.keyCode !== 229 && !event.repeat
}
