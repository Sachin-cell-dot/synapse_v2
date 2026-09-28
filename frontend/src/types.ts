export type NullableNumber = number | null
export type ForecastVariable = 'rainfall' | 'tmax' | 'tmin' | 'wind_max'
export type ForecastSource = 'blend' | 'gfs' | 'ifs_hres' | 'aifs' | 'gefs_mean' | 'gefs_spread'
export type DashboardMode = 'historical' | 'operational'

export interface VariableValues {
  blend: NullableNumber
  gfs: NullableNumber
  ifs: NullableNumber
  aifs: NullableNumber
  unit: string
  status: string | null
  method: string | null
  eligibility: string | null
  coverage: NullableNumber
}

export interface VerificationMetric {
  method: string
  n: number
  mae: number
  rmse: number
  bias: number
  heavyRainCsi: NullableNumber
}

export interface SynopStationSummary {
  stationId: string
  latitude: number
  longitude: number
  observationCount: number
  firstObservationUtc: string
  lastObservationUtc: string
}

export interface SynopVerificationMetric {
  scope: string
  stationId: string
  variableId: string
  sourceId: string
  leadHours: number
  n: number
  days: number
  issueTimeStatus: string
  measurementHeightStatus: string
  mae: number
  rmse: number
  bias: number
}

export interface SynopticContext {
  availableStations: NullableNumber
  maxAgeHours: NullableNumber
  latestTimestamp: string | null
}

export interface DistrictForecast {
  district: string
  districtCode: string
  division: string | null
  date: string
  issueTime: string | null
  gfs: NullableNumber
  historicalForecast: NullableNumber
  historicalTrust: NullableNumber
  ifs: NullableNumber
  aifs: NullableNumber
  predictedErrorGfs: NullableNumber
  predictedErrorIfs: NullableNumber
  predictedErrorAifs: NullableNumber
  trustGfs: NullableNumber
  trustIfs: NullableNumber
  trustAifs: NullableNumber
  finalForecast: NullableNumber
  actualRainfall: NullableNumber
  absoluteError: NullableNumber
  modelAgreement: NullableNumber
  confidence: string | null
  trustExplanation: string | null
  rainfallCategory: string | null
  synopticContext: SynopticContext | null
  dataMode: 'historical_proxy' | 'operational_forecast' | 'archived_reconstruction'
  leadDays: NullableNumber
  operationalStatus: string | null
  blendMethod: string | null
  artifactVersion: string | null
  verificationStatus: string | null
  gefsMean: NullableNumber
  gefsSpread: NullableNumber
  gefsRole: string | null
  variables: Record<'tmax' | 'tmin' | 'wind_max', VariableValues>
}

export interface DataStatus {
  masterLoaded: boolean
  errorOutputLoaded: boolean
  geoJsonLoaded: boolean
  geoDistrictCount: number | null
  duplicateGeoDistrictNames: string[]
  errors: string[]
}

export interface ForecastStore {
  records: DistrictForecast[]
  futureGfsForecasts: FutureGfsForecast[]
  dates: string[]
  districts: string[]
  status: DataStatus
  verificationMetrics: VerificationMetric[]
  synopStations: SynopStationSummary[]
  synopVerificationMetrics: SynopVerificationMetric[]
}

/** A real GFS grid-context forecast that is outside the audited IMD hindcast contract. */
export interface FutureGfsForecast {
  date: string
  issueTime: string
  validTime: string
  regionalGfs: number
  gridCellCount: number
}

export interface RainCategory {
  label: string
  color: string
  min: number
  max: number | null
}
