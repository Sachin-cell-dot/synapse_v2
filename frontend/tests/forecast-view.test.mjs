import test from 'node:test'
import assert from 'node:assert/strict'
import { datesForMode, HELD_OUT_COMPARISON, rainfallBlendExplanation, recordsForSelection, SOURCE_FORECAST_RANGE_LABEL, sourceForecastRangeLabel, thresholdCoverage, valueFor, verificationLabel } from '../src/utils/forecastView.ts'
import { categoryForRainfall, displayRainfall } from '../src/utils/rainfall.ts'

const record = {
  district: 'Dakshina Kannada', date: '2026-09-27', leadDays: 1,
  finalForecast: 20, gfs: 10, ifs: 20, aifs: 30, actualRainfall: null,
  predictedErrorGfs: 0.1, predictedErrorIfs: 0.2579475641, predictedErrorAifs: 1.0454380512,
  trustGfs: 0.6741591031, trustIfs: 0.2613550957, trustAifs: 0.0644858012,
  blendMethod: 'xgboost_expected_absolute_error', artifactVersion: '2e070898b34c589b',
  gefsMean: 18, gefsSpread: 4,
  variables: {
    tmax: { blend: 39, gfs: 38, ifs: 39, aifs: 40 },
    tmin: { blend: 20, gfs: 19, ifs: 20, aifs: 21 },
    wind_max: { blend: 13, gfs: 12, ifs: 13, aifs: 14 },
  },
}

test('source selectors keep GEFS outside the deterministic blend', () => {
  assert.equal(valueFor(record, 'rainfall', 'blend'), 20)
  assert.equal(valueFor(record, 'rainfall', 'gefs_mean'), 18)
  assert.equal(valueFor(record, 'rainfall', 'gefs_spread'), 4)
  assert.equal(valueFor(record, 'tmax', 'gefs_mean'), null)
})

test('threshold coverage uses only available district blends', () => {
  const result = thresholdCoverage([record, { ...record, variables: { ...record.variables, wind_max: { ...record.variables.wind_max, blend: null } } }], 'wind_max', 12, 31)
  assert.deepEqual(result, { available: 1, expected: 31, exceedances: 1 })
})

test('mode and date selection never mix proxy evaluation with issued cycles', () => {
  const records = [
    { ...record, date: '2026-08-31', dataMode: 'historical_proxy', leadDays: 1, actualRainfall: 12 },
    { ...record, date: '2026-09-27', dataMode: 'operational_forecast', leadDays: 1, actualRainfall: null },
    { ...record, date: '2026-09-28', dataMode: 'operational_forecast', leadDays: 2, actualRainfall: null },
  ]
  assert.deepEqual(datesForMode(records, 'historical'), ['2026-08-31'])
  assert.deepEqual(datesForMode(records, 'operational'), ['2026-09-27', '2026-09-28'])
  assert.deepEqual(recordsForSelection(records, 'historical', '2026-08-31', 1).map((item) => item.dataMode), ['historical_proxy'])
  const operational = recordsForSelection(records, 'operational', '2026-09-27', 1)
  assert.equal(operational[0].actualRainfall, null)
  assert.equal(verificationLabel(operational[0]), 'Verification pending')
})

test('forecast card uses a source min/max range without confidence wording', () => {
  assert.equal(SOURCE_FORECAST_RANGE_LABEL, 'Source forecast range')
  assert.equal(sourceForecastRangeLabel(record), '10.0–30.0 mm')
})

test('sub-tenth rainfall display remains consistent with the Dry category', () => {
  assert.equal(displayRainfall(0), '0.0 mm')
  assert.equal(displayRainfall(0.05), '<0.1 mm')
  assert.equal(categoryForRainfall(0.05)?.label, 'Dry')
  assert.equal(displayRainfall(0.1), '0.1 mm')
  assert.equal(categoryForRainfall(0.1)?.label, 'Very light')
})

test('Day-1 explanation reports persisted expected errors, weights, and contextual influence', () => {
  const explanation = rainfallBlendExplanation(record)
  assert.match(explanation, /GFS: 0\.10 mm expected error → 67\.4% weight/)
  assert.match(explanation, /IFS HRES: 0\.26 mm expected error → 26\.1% weight/)
  assert.match(explanation, /AIFS: 1\.05 mm expected error → 6\.4% weight/)
  assert.match(explanation, /GFS is the strongest influence for Dakshina Kannada on 2026-09-27 at Day 1 according to the model/)
})

test('Days 2–6 explain the equal-weight baseline without preference', () => {
  for (const leadDays of [2, 3, 4, 5, 6]) {
    assert.equal(rainfallBlendExplanation({ ...record, leadDays, blendMethod: 'equal_weight_baseline' }), `Day-${leadDays} uses an equal-weight baseline (GFS, IFS HRES, and AIFS: 33.3% each), with no model preference.`)
  }
})

test('missing evidence produces no invented explanation', () => {
  assert.equal(rainfallBlendExplanation({ ...record, predictedErrorAifs: null }), 'Explanation unavailable')
  assert.equal(rainfallBlendExplanation({ ...record, artifactVersion: null }), 'Explanation unavailable')
})

test('ties name every strongest influence', () => {
  const explanation = rainfallBlendExplanation({ ...record, trustGfs: 0.4, trustIfs: 0.2, trustAifs: 0.4 })
  assert.match(explanation, /GFS and AIFS are tied as the strongest influences/)
})

test('copy makes no universal best-model claim and keeps held-out metrics separate', () => {
  const explanation = rainfallBlendExplanation(record)
  assert.doesNotMatch(explanation, /\b(?:GFS|IFS HRES|AIFS) is (?:the )?best\b/i)
  assert.equal(HELD_OUT_COMPARISON, 'The XGBoost blend has the lowest overall rainfall MAE. AIFS leads on RMSE, absolute bias, and 50 mm heavy-rain CSI.')
})
