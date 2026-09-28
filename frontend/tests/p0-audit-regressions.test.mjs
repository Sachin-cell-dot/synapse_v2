import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { parseCsv } from '../src/data/csv.ts'
import { categoryCounts, categoryForRainfall } from '../src/utils/rainfall.ts'
import { synopPairSummary } from '../src/utils/forecastView.ts'

test('rainfall categories are exhaustive and count all 31 statewide Day-1 districts', () => {
  for (const value of [0, 0.01, 0.05, 0.0999, 0.1, 2.45, 15.55, 35.55, 64.45, 115.55, 204.45, 1000]) {
    assert.notEqual(categoryForRainfall(value), null, `uncategorized rainfall value ${value}`)
  }
  const path = new URL('../../outputs/operational/exports/synapse_wx_cycle_bc5f2a5972e54e5da32d11f893c6b229.csv', import.meta.url)
  const dayOne = parseCsv(readFileSync(path, 'utf8')).filter((row) => row.lead_days === '1')
  const counts = categoryCounts(dayOne.map((row) => ({ value: Number(row.synapse_wx_forecast_mm) })))
  assert.equal(dayOne.length, 31)
  assert.equal([...counts.values()].reduce((sum, count) => sum + count, 0), 31)
})

test('SYNOP summary counts full availability once and keeps common denominators separate', () => {
  const path = new URL('../../outputs/operational/exports/synop/synop_station_metrics.csv', import.meta.url)
  const metrics = parseCsv(readFileSync(path, 'utf8')).map((row) => ({
    scope: row.scope,
    variableId: row.variable_id,
    n: Number(row.n),
  }))
  assert.deepEqual(synopPairSummary(metrics), {
    fullMatchedPairs: 21998,
    commonTemperaturePairs: 2427,
    commonWindPairs: 2477,
  })
})

test('historical mode source contains only genuine matched proxy-evaluation rows', () => {
  const path = new URL('../../outputs/operational/exports/holdout/day1-holdout-v1-2e070898b34c589b.predictions.csv', import.meta.url)
  const rows = parseCsv(readFileSync(path, 'utf8'))
  assert.equal(rows.length, 3749)
  assert.ok(rows.every((row) => row.historical_run_identity === 'unknown_legacy_daily_forecast_alignment_not_exact_run'))
  assert.ok(rows.every((row) => row.date && row.district_id && row.imd_actual_mm !== '' && row.xgboost_blend_mm !== ''))
})
