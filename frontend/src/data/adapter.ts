import type { DistrictForecast, ForecastStore, SynopStationSummary, SynopVerificationMetric, VariableValues, VerificationMetric } from '../types'
import { hasRequiredColumns, isCsvContentType, numberOrNull, parseCsv } from './csv'

const HISTORICAL_URL = '/operational/exports/holdout/day1-holdout-v1-2e070898b34c589b.predictions.csv'
const OPERATIONAL_URL = '/synapse_wx_latest_operational_cycle.csv'
const SCORECARD_URL = '/operational/exports/holdout/day1-holdout-v1-2e070898b34c589b.scorecard.csv'
const SYNOP_STATIONS_URL = '/operational/exports/synop/synop_stations.csv'
const SYNOP_METRICS_URL = '/operational/exports/synop/synop_station_metrics.csv'
const HISTORICAL_REQUIRED_COLUMNS = ['historical_run_identity', 'date', 'district_id', 'district', 'region', 'imd_actual_mm', 'gfs_rain_mm', 'ifs_hres_rain_mm', 'aifs_rain_mm', 'xgboost_blend_mm']
const OPERATIONAL_REQUIRED_COLUMNS = ['district', 'district_id', 'valid_start_utc', 'issued_at_utc', 'lead_days', 'status', 'mode', 'source_gfs_mm', 'source_ifs_hres_mm', 'source_aifs_mm', 'synapse_wx_forecast_mm']
const SCORECARD_REQUIRED_COLUMNS = ['metric_family', 'scope_type', 'method', 'n', 'mae_mm', 'rmse_mm', 'bias_mm']

function variableValues(row: Record<string, string>, variable: string, statistic: string, unit: string): VariableValues {
  const prefix = `${variable}_${statistic}`
  return {
    blend: numberOrNull(row[`blend_${prefix}_value`]),
    gfs: numberOrNull(row[`source_gfs_${prefix}_value`]),
    ifs: numberOrNull(row[`source_ifs_hres_${prefix}_value`]),
    aifs: numberOrNull(row[`source_aifs_${prefix}_value`]),
    unit,
    status: row[`blend_${prefix}_status`] || null,
    method: row[`blend_${prefix}_method`] || null,
    eligibility: row[`blend_${prefix}_blend_eligibility`] || null,
    coverage: numberOrNull(row[`source_gfs_${prefix}_point_coverage`]),
  }
}

export function parseHeldOutScorecard(text: string): VerificationMetric[] {
  const rows = parseCsv(text)
  const overall = rows.filter((row) => row.metric_family === 'continuous' && row.scope_type === 'overall')
  const heavy = new Map(rows.filter((row) => row.metric_family === 'event' && row.scope_type === 'overall' && Number(row.threshold_mm) === 50).map((row) => [row.method, numberOrNull(row.csi)]))
  return overall.map((row) => ({ method: row.method, n: Number(row.n), mae: Number(row.mae_mm), rmse: Number(row.rmse_mm), bias: Number(row.bias_mm), heavyRainCsi: heavy.get(row.method) ?? null }))
}

function localValidDate(value: string): string {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? ''
  return `${part('year')}-${part('month')}-${part('day')}`
}

function spread(values: Array<number | null>): number | null {
  const available = values.filter((value): value is number => value !== null && Number.isFinite(value))
  return available.length > 1 ? Math.max(...available) - Math.min(...available) : null
}

export async function loadForecastStore(): Promise<ForecastStore> {
  const [response, operationalResponse, scorecardResponse, synopStationsResponse, synopMetricsResponse] = await Promise.all([fetch(HISTORICAL_URL), fetch(OPERATIONAL_URL, { cache: 'no-store' }), fetch(SCORECARD_URL), fetch(SYNOP_STATIONS_URL), fetch(SYNOP_METRICS_URL)])
  const historicalRows = response.ok && isCsvContentType(response.headers.get('content-type')) ? parseCsv(await response.text()) : []
  const historicalAvailable = hasRequiredColumns(historicalRows, HISTORICAL_REQUIRED_COLUMNS)
  const operationalRows = operationalResponse.ok && isCsvContentType(operationalResponse.headers.get('content-type')) ? parseCsv(await operationalResponse.text()) : []
  const operationalAvailable = hasRequiredColumns(operationalRows, OPERATIONAL_REQUIRED_COLUMNS)
  const scorecardText = scorecardResponse.ok && isCsvContentType(scorecardResponse.headers.get('content-type')) ? await scorecardResponse.text() : ''
  const scorecardAvailable = hasRequiredColumns(parseCsv(scorecardText), SCORECARD_REQUIRED_COLUMNS)
  const synopStationRows = synopStationsResponse.ok && isCsvContentType(synopStationsResponse.headers.get('content-type')) ? parseCsv(await synopStationsResponse.text()) : []
  const synopMetricRows = synopMetricsResponse.ok && isCsvContentType(synopMetricsResponse.headers.get('content-type')) ? parseCsv(await synopMetricsResponse.text()) : []
  const synopStations: SynopStationSummary[] = synopStationRows.map((row) => ({ stationId: row.station_id, latitude: Number(row.latitude), longitude: Number(row.longitude), observationCount: Number(row.observation_count), firstObservationUtc: row.first_observation_utc, lastObservationUtc: row.last_observation_utc }))
  const synopVerificationMetrics: SynopVerificationMetric[] = synopMetricRows.map((row) => ({ scope: row.scope, stationId: row.station_id, variableId: row.variable_id, sourceId: row.source_id, leadHours: Number(row.lead_hours), n: Number(row.n), days: Number(row.days), issueTimeStatus: row.issue_time_status, measurementHeightStatus: row.measurement_height_status, mae: Number(row.mae), rmse: Number(row.rmse), bias: Number(row.bias) }))

  const historicalRecords: DistrictForecast[] = historicalRows.map((row) => ({
    district: row.district, districtCode: row.district_id, division: row.region || null, date: row.date, issueTime: null,
    gfs: numberOrNull(row.gfs_rain_mm), historicalForecast: null, historicalTrust: null,
    ifs: numberOrNull(row.ifs_hres_rain_mm), aifs: numberOrNull(row.aifs_rain_mm),
    predictedErrorGfs: numberOrNull(row.xgboost_predicted_error_gfs_mm),
    predictedErrorIfs: numberOrNull(row.xgboost_predicted_error_ifs_hres_mm),
    predictedErrorAifs: numberOrNull(row.xgboost_predicted_error_aifs_mm),
    trustGfs: numberOrNull(row.xgboost_weight_gfs), trustIfs: numberOrNull(row.xgboost_weight_ifs_hres), trustAifs: numberOrNull(row.xgboost_weight_aifs),
    finalForecast: numberOrNull(row.xgboost_blend_mm), actualRainfall: numberOrNull(row.imd_actual_mm),
    absoluteError: numberOrNull(row.xgboost_blend_mm) !== null && numberOrNull(row.imd_actual_mm) !== null ? Math.abs(Number(row.xgboost_blend_mm) - Number(row.imd_actual_mm)) : null,
    modelAgreement: spread([numberOrNull(row.gfs_rain_mm), numberOrNull(row.ifs_hres_rain_mm), numberOrNull(row.aifs_rain_mm)]),
    confidence: null, trustExplanation: 'Frozen held-out historical proxy row; exact upstream run identity is unverified.',
    rainfallCategory: null, synopticContext: null,
    dataMode: 'historical_proxy', leadDays: 1, operationalStatus: null, blendMethod: 'xgboost_expected_absolute_error', artifactVersion: row.artifact_version || null,
    verificationStatus: 'available', gefsMean: null, gefsSpread: null, gefsRole: null,
    variables: {
      tmax: { blend: null, gfs: null, ifs: null, aifs: null, unit: '°C', status: null, method: null, eligibility: null, coverage: null },
      tmin: { blend: null, gfs: null, ifs: null, aifs: null, unit: '°C', status: null, method: null, eligibility: null, coverage: null },
      wind_max: { blend: null, gfs: null, ifs: null, aifs: null, unit: 'm/s', status: null, method: null, eligibility: null, coverage: null },
    },
  }))

  const operationalRecords: DistrictForecast[] = operationalRows.filter((row) => row.status === 'complete').map((row) => {
    const gfs = numberOrNull(row.source_gfs_mm)
    const ifs = numberOrNull(row.source_ifs_hres_mm)
    const aifs = numberOrNull(row.source_aifs_mm)
    const leadDays = numberOrNull(row.lead_days)
    return {
      district: row.district, districtCode: row.district_id, division: row.division || null,
      date: localValidDate(row.valid_start_utc), issueTime: row.issued_at_utc || null,
      gfs, historicalForecast: null, historicalTrust: null, ifs, aifs,
      predictedErrorGfs: numberOrNull(row.predicted_error_gfs_mm),
      predictedErrorIfs: numberOrNull(row.predicted_error_ifs_hres_mm),
      predictedErrorAifs: numberOrNull(row.predicted_error_aifs_mm),
      trustGfs: numberOrNull(row.weight_gfs), trustIfs: numberOrNull(row.weight_ifs_hres), trustAifs: numberOrNull(row.weight_aifs),
      finalForecast: numberOrNull(row.synapse_wx_forecast_mm), actualRainfall: numberOrNull(row.verification_mm),
      absoluteError: numberOrNull(row.synapse_wx_absolute_error_mm),
      modelAgreement: spread([gfs, ifs, aifs]), confidence: null,
      trustExplanation: `Operational Day-${leadDays ?? '—'} forecast. Weighting status: ${row.fallback || 'adaptive historical skill'}.`,
      rainfallCategory: null, synopticContext: null,
      dataMode: row.mode.toLowerCase().includes('reconstruction') ? 'archived_reconstruction' : 'operational_forecast',
      leadDays, operationalStatus: row.status, blendMethod: row.blend_method || null, artifactVersion: row.artifact_version || null,
      verificationStatus: row.verification_status || null,
      gefsMean: numberOrNull(row.source_gfs_ens_mm), gefsSpread: numberOrNull(row.source_gfs_ens_ensemble_spread_mm),
      gefsRole: row.source_gfs_ens_blend_role || null,
      variables: {
        tmax: variableValues(row, 'temperature_2m', 'maximum', '°C'),
        tmin: variableValues(row, 'temperature_2m', 'minimum', '°C'),
        wind_max: variableValues(row, 'wind_speed_10m', 'maximum', 'm/s'),
      },
    }
  })

  const records = [...historicalRecords, ...operationalRecords].sort((a, b) => a.date.localeCompare(b.date) || a.district.localeCompare(b.district))
  const errors = []
  if (!historicalAvailable) errors.push(`${HISTORICAL_URL}: matched historical forecast/IMD rows unavailable or invalid`)
  if (!operationalAvailable) errors.push(`${OPERATIONAL_URL}: operational CSV unavailable or invalid`)
  if (!scorecardAvailable) errors.push(`${SCORECARD_URL}: held-out scorecard unavailable or invalid`)
  return {
    records, futureGfsForecasts: [], verificationMetrics: scorecardAvailable ? parseHeldOutScorecard(scorecardText) : [], synopStations, synopVerificationMetrics,
    dates: [...new Set(records.map((record) => record.date))].sort(),
    districts: [...new Set(records.map((record) => record.district))].sort((a, b) => a.localeCompare(b)),
    status: { masterLoaded: historicalAvailable, errorOutputLoaded: operationalAvailable, geoJsonLoaded: false, geoDistrictCount: null, duplicateGeoDistrictNames: [], errors },
  }
}
