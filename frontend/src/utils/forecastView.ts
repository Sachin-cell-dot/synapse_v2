import type { DashboardMode, DistrictForecast, ForecastSource, ForecastVariable, NullableNumber, SynopVerificationMetric } from '../types'

export const VARIABLE_LABELS: Record<ForecastVariable, string> = {
  rainfall: 'Rainfall', tmax: 'Daily Tmax', tmin: 'Daily Tmin', wind_max: 'Maximum 10 m wind',
}

export const SOURCE_LABELS: Record<ForecastSource, string> = {
  blend: 'SYNAPSE-WX blend', gfs: 'GFS', ifs_hres: 'IFS HRES', aifs: 'AIFS',
  gefs_mean: 'GEFS mean · display only', gefs_spread: 'GEFS spread · display only',
}
export const SOURCE_FORECAST_RANGE_LABEL = 'Source forecast range'
export const HELD_OUT_COMPARISON = 'The XGBoost blend has the lowest overall rainfall MAE. AIFS leads on RMSE, absolute bias, and 50 mm heavy-rain CSI.'

/** Min/max across available deterministic sources; not calibrated uncertainty. */
export function sourceForecastRangeLabel(record: DistrictForecast): string {
  const models = [record.gfs, record.ifs, record.aifs].filter((value): value is number => value !== null && Number.isFinite(value))
  if (models.length < 2) return 'Not available yet'
  return `${Math.min(...models).toFixed(1)}–${Math.max(...models).toFixed(1)} mm`
}

export function valueFor(record: DistrictForecast | null | undefined, variable: ForecastVariable, source: ForecastSource, historical = false): NullableNumber {
  if (!record) return null
  if (historical) return variable === 'rainfall' ? record.actualRainfall : null
  if (variable === 'rainfall') {
    if (source === 'blend') return record.finalForecast
    if (source === 'gfs') return record.gfs
    if (source === 'ifs_hres') return record.ifs
    if (source === 'aifs') return record.aifs
    if (source === 'gefs_mean') return record.gefsMean
    return record.gefsSpread
  }
  if (source === 'gefs_mean' || source === 'gefs_spread') return null
  const values = record.variables[variable]
  return source === 'blend' ? values.blend : source === 'gfs' ? values.gfs : source === 'ifs_hres' ? values.ifs : values.aifs
}

export function unitFor(variable: ForecastVariable): string {
  return variable === 'rainfall' ? 'mm' : variable === 'wind_max' ? 'm/s' : '°C'
}

export function thresholdCoverage(records: DistrictForecast[], variable: ForecastVariable, threshold: number, expectedDistricts: number) {
  const values = records.map((record) => valueFor(record, variable, 'blend')).filter((value): value is number => value !== null && Number.isFinite(value))
  return { available: values.length, expected: expectedDistricts, exceedances: values.filter((value) => value >= threshold).length }
}

export function synopPairSummary(metrics: SynopVerificationMetric[]) {
  const full = metrics.filter((metric) => metric.scope === 'full_availability')
  const common = metrics.filter((metric) => metric.scope === 'common_three_source')
  const commonFor = (variableId: string) => {
    const rows = common.filter((metric) => metric.variableId === variableId)
    return rows.length ? Math.min(...rows.map((metric) => metric.n)) : 0
  }
  return {
    fullMatchedPairs: full.reduce((sum, metric) => sum + metric.n, 0),
    commonTemperaturePairs: commonFor('temperature_2m'),
    commonWindPairs: commonFor('wind_speed_10m'),
  }
}

export function datesForMode(records: DistrictForecast[], mode: DashboardMode): string[] {
  const dataMode = mode === 'historical' ? 'historical_proxy' : 'operational_forecast'
  return [...new Set(records.filter((record) => record.dataMode === dataMode).map((record) => record.date))].sort()
}

export function recordsForSelection(records: DistrictForecast[], mode: DashboardMode, date: string, lead: number): DistrictForecast[] {
  const dataMode = mode === 'historical' ? 'historical_proxy' : 'operational_forecast'
  return records.filter((record) => record.dataMode === dataMode && record.date === date && (mode === 'historical' || record.leadDays === lead))
}

export function verificationLabel(record: DistrictForecast): string {
  return record.actualRainfall === null ? 'Verification pending' : 'Verification available'
}

export function rainfallBlendExplanation(record: DistrictForecast): string {
  if (record.leadDays !== null && record.leadDays >= 2 && record.leadDays <= 6 && record.blendMethod === 'equal_weight_baseline') {
    return `Day-${record.leadDays} uses an equal-weight baseline (GFS, IFS HRES, and AIFS: 33.3% each), with no model preference.`
  }
  if (record.leadDays !== 1 || record.blendMethod !== 'xgboost_expected_absolute_error' || !record.artifactVersion) return 'Explanation unavailable'

  const sources = [
    { name: 'GFS', error: record.predictedErrorGfs, weight: record.trustGfs },
    { name: 'IFS HRES', error: record.predictedErrorIfs, weight: record.trustIfs },
    { name: 'AIFS', error: record.predictedErrorAifs, weight: record.trustAifs },
  ]
  if (sources.some(({ error, weight }) => error === null || !Number.isFinite(error) || error <= 0 || weight === null || !Number.isFinite(weight) || weight < 0)) return 'Explanation unavailable'
  const total = sources.reduce((sum, source) => sum + (source.weight ?? 0), 0)
  if (Math.abs(total - 1) > 0.001) return 'Explanation unavailable'

  const maximum = Math.max(...sources.map((source) => source.weight ?? 0))
  const leaders = sources.filter((source) => Math.abs((source.weight ?? 0) - maximum) < 1e-9).map((source) => source.name)
  const leaderText = leaders.length === 1 ? `${leaders[0]} is the strongest influence` : `${leaders.join(' and ')} are tied as the strongest influences`
  const evidence = sources.map((source) => `${source.name}: ${source.error!.toFixed(2)} mm expected error → ${(source.weight! * 100).toFixed(1)}% weight`).join('; ')
  return `Locked Day-1 XGBoost gives lower predicted-error sources higher normalized weight. ${evidence}. ${leaderText} for ${record.district} on ${record.date} at Day 1 according to the model. This influence statement applies only to this case.`
}
