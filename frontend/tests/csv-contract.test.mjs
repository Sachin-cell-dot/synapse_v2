import assert from 'node:assert/strict'
import test from 'node:test'

import { hasRequiredColumns, isCsvContentType, parseCsv } from '../src/data/csv.ts'

test('Vite HTML fallback is not accepted as a forecast CSV', () => {
  const rows = parseCsv('<!doctype html><html><body>Vite app</body></html>')
  assert.equal(isCsvContentType('text/html'), false)
  assert.equal(hasRequiredColumns(rows, ['district', 'date']), false)
})

test('forecast CSV is accepted only when required columns are present', () => {
  const rows = parseCsv('district,date,source_gfs_mm\nBengaluru Urban,2026-09-27,4.2\n')
  assert.equal(isCsvContentType('text/csv; charset=utf-8'), true)
  assert.equal(hasRequiredColumns(rows, ['district', 'date', 'source_gfs_mm']), true)
  assert.equal(hasRequiredColumns(rows, ['district', 'date', 'source_aifs_mm']), false)
})
