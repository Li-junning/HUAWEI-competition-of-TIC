export function filePresentation(filename?: string | null): { kind: string; badge: string; label: string } {
  const extension = filename?.split('.').pop()?.toLowerCase()
  if (extension === 'pdf') return { kind: 'pdf', badge: 'PDF', label: 'PDF 文档' }
  if (extension === 'doc' || extension === 'docx') return { kind: 'word', badge: 'W', label: 'Word 文档' }
  if (extension === 'md' || extension === 'markdown') return { kind: 'markdown', badge: 'MD', label: 'Markdown 文档' }
  if (!filename || extension === 'txt') return { kind: 'text', badge: 'TXT', label: '纯文本文件' }
  return { kind: 'generic', badge: 'FILE', label: '其他文件' }
}
