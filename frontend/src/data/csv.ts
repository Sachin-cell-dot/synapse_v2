export type CsvRow = Record<string, string>

// Generated SYNAPSE-WX files do not contain quoted multiline fields. This
// parser supports quoted commas while keeping the client bundle dependency-free.
export function parseCsv(text: string): CsvRow[] {
  const rows: string[][] = []
  let current: string[] = []
  let field = ''
  let quoted = false
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index]
    if (character === '"') {
      if (quoted && text[index + 1] === '"') { field += '"'; index += 1 } else quoted = !quoted
    } else if (character === ',' && !quoted) { current.push(field); field = ''
    } else if ((character === '\n' || character === '\r') && !quoted) {
      if (character === '\r' && text[index + 1] === '\n') index += 1
      current.push(field)
      if (current.some((value) => value.length)) rows.push(current)
      current = []; field = ''
    } else field += character
  }
  if (field.length || current.length) { current.push(field); rows.push(current) }
  const [headers, ...body] = rows
  if (!headers) return []
  return body.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ''])))
}

export function numberOrNull(value: string | undefined): number | null {
  if (value === undefined || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function hasRequiredColumns(rows: CsvRow[], requiredColumns: readonly string[]): boolean {
  if (rows.length === 0) return false
  return requiredColumns.every((column) => Object.prototype.hasOwnProperty.call(rows[0], column))
}

export function isCsvContentType(contentType: string | null): boolean {
  return contentType?.toLowerCase().includes('text/csv') ?? false
}
