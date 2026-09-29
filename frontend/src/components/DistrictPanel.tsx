import type { DistrictForecast, ForecastSource, ForecastVariable, FutureGfsForecast, NullableNumber } from '../types'
import { categoryForRainfall, displayRainfall, shortDate } from '../utils/rainfall'
import {
  formatExpectedError,
  formatTrust,
} from '../utils/forecastPresentation'
import { RainfallChart } from './RainfallChart'
import { SOURCE_FORECAST_RANGE_LABEL, SOURCE_LABELS, VARIABLE_LABELS, rainfallBlendExplanation, sourceForecastRangeLabel, unitFor, valueFor, verificationLabel } from '../utils/forecastView'

interface Props {
  record: DistrictForecast | null
  history: DistrictForecast[]
  historical: boolean
  futureGfsForecast: FutureGfsForecast | null
  districtName: string | null
  variable: ForecastVariable
  source: ForecastSource
  onClose: () => void
}

function Source({
  name,
  value,
  weight,
  color,
  unit = 'mm',
  showWeight = true,
}: {
  name: string
  value: number | null
  weight: number | null
  color: string
  unit?: string
  showWeight?: boolean
}) {
  return (
    <div className="source-model" style={{ '--source': color } as React.CSSProperties}>
      <div>
        <span>{name}</span>
        <b>{value === null ? 'No forecast' : unit === 'mm' ? displayRainfall(value) : `${value.toFixed(1)} ${unit}`}</b>
      </div>
      {showWeight && <div>
        <small>Adaptive trust</small>
        <strong>{weight === null ? 'Not available yet' : `${(weight * 100).toFixed(1)}%`}</strong>
      </div>}
      {showWeight && <i>
        <em style={{ width: `${(weight ?? 0) * 100}%` }} />
      </i>}
    </div>
  )
}

function TrustTable({ record }: { record: DistrictForecast }) {
  const rows: Array<{ name: string; error: NullableNumber; trust: NullableNumber }> = [
    { name: 'GFS', error: record.predictedErrorGfs, trust: record.trustGfs },
    { name: 'IFS HRES', error: record.predictedErrorIfs, trust: record.trustIfs },
    { name: 'AIFS', error: record.predictedErrorAifs, trust: record.trustAifs },
  ]
  const hasRealGfs = record.trustGfs !== null || record.predictedErrorGfs !== null

  return (
    <div className="trust-table-block">
      <h3>Adaptive model trust</h3>
      <table className="trust-table">
        <thead>
          <tr>
            <th>Model</th>
            <th>Expected error</th>
            <th>Trust</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{formatExpectedError(row.error)}</td>
              <td>{formatTrust(row.trust)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {hasRealGfs && (
        <p className="trust-table-note">
          SYNAPSE-WX dynamically determines which NWP model deserves more trust for each forecast situation.
        </p>
      )}
    </div>
  )
}

export function DistrictPanel({ record, history, districtName, variable, source, onClose }: Props) {
  if (!record) {
    return (
      <aside className="district-panel card empty-panel panel-enter">
        <p className="eyebrow">District intelligence</p>
        <h2>{districtName ?? 'Select a district'}</h2>
        <p>{districtName ? 'No forecast is available for this district in the selected one-district operational cycle.' : 'Click a district on the map or use search to inspect its forecast.'}</p>
      </aside>
    )
  }

  const value = valueFor(record, variable, source)
  const category = categoryForRainfall(value)
  const operational = record.dataMode === 'operational_forecast'
  const reconstructed = record.dataMode === 'archived_reconstruction'
  const total = (record.trustGfs ?? 0) + (record.trustIfs ?? 0) + (record.trustAifs ?? 0)
  const rangeText = sourceForecastRangeLabel(record)

  return (
    <aside className="district-panel card panel-enter" key={`${record.district}-${record.date}`}>
      {districtName && (
        <button className="close-panel" onClick={onClose} aria-label="Close district panel">
          ×
        </button>
      )}
      <p className="eyebrow">
        {record.division} · #{record.districtCode}
      </p>
      <h2>{record.district}</h2>
      <div className="hero-rain">
        <span>
          {variable !== 'rainfall' ? `${VARIABLE_LABELS[variable]} · ${SOURCE_LABELS[source]}` : operational
            ? `SYNAPSE-WX OPERATIONAL DAY-${record.leadDays ?? '—'}`
            : reconstructed
              ? 'SYNAPSE-WX 24-HOUR FORECAST'
              : 'HISTORICAL PROXY EVALUATION'}
        </span>
        <strong className="hero-rain-value">{value === null ? 'No forecast' : variable === 'rainfall' ? displayRainfall(value) : `${value.toFixed(1)} ${unitFor(variable)}`}</strong>
        {variable === 'rainfall' && <div className="hero-meta">
          <p>
            {SOURCE_FORECAST_RANGE_LABEL}: <b>{rangeText}</b>
          </p>
        </div>}
        <em>
          {variable === 'rainfall' ? category?.label ?? 'Dry' : 'Experimental forecast only · observations unavailable'}
          {record.confidence ? ` · ${record.confidence}` : ''}
        </em>
      </div>
      <dl className="detail-list">
        <div>
          <dt>Valid date</dt>
          <dd>{shortDate(record.date)}</dd>
        </div>
        {variable === 'rainfall' && <div>
          <dt>Model spread</dt>
          <dd>{displayRainfall(record.modelAgreement)}</dd>
        </div>}
      </dl>
      {variable === 'rainfall' && <div className="trust-total">
        <span>Adaptive trust mix</span>
        <b>{(total * 100).toFixed(1)}%</b>
      </div>}
      {variable === 'rainfall' ? <div className="source-list">
        <Source name="GFS" value={record.gfs} weight={record.trustGfs} color="#4f9dff" />
        <Source name="IFS HRES" value={record.ifs} weight={record.trustIfs} color="#ffc46b" />
        <Source name="AIFS" value={record.aifs} weight={record.trustAifs} color="#a87cff" />
      </div> : <div className="source-list">
        <Source name="GFS" value={record.variables[variable].gfs} weight={null} unit={unitFor(variable)} showWeight={false} color="#4f9dff" />
        <Source name="IFS HRES" value={record.variables[variable].ifs} weight={null} unit={unitFor(variable)} showWeight={false} color="#ffc46b" />
        <Source name="AIFS" value={record.variables[variable].aifs} weight={null} unit={unitFor(variable)} showWeight={false} color="#a87cff" />
      </div>}
      {variable === 'rainfall' && <TrustTable record={record} />}
      {variable === 'rainfall' && record.gefsMean !== null && <div className="verification-block"><p className="eyebrow">GEFS display-only ensemble</p><small>Not a fourth locked weight; spread is not a calibrated probability.</small><div><span>Ensemble mean <b>{displayRainfall(record.gefsMean)}</b></span><span>Spread proxy <b>{displayRainfall(record.gefsSpread)}</b></span></div></div>}
      {variable === 'rainfall' && !operational && record.actualRainfall !== null && (
        <div className="verification-block">
          <p className="eyebrow">Verification only</p>
          <small>Post-forecast IMD observation — never an input to this forecast</small>
          <div>
            <span>
              IMD realised rainfall <b>{displayRainfall(record.actualRainfall)}</b>
            </span>
            <span>
              Absolute error <b>{displayRainfall(record.absoluteError)}</b>
            </span>
          </div>
        </div>
      )}
      {variable === 'rainfall' && (operational || reconstructed) && record.actualRainfall === null && (
        <div className="verification-block">
          <p className="eyebrow">{verificationLabel(record)}</p>
          <small>No matching IMD realised-rainfall observation has been imported for this valid date.</small>
        </div>
      )}
      {variable === 'rainfall' && <h3>{operational || reconstructed ? 'Recent historical performance' : 'May–August performance'}</h3>}
      {variable === 'rainfall' && <RainfallChart records={history.filter((item) => item.dataMode === 'historical_proxy')} />}
      <div className="trust-explanation">
        <b>Why this blend?</b>
        <p>{variable === 'rainfall' ? rainfallBlendExplanation(record) : 'All three deterministic sources are combined with equal weights. This is an experimental forecast-only product because statewide district observations are unavailable.'}</p>
      </div>
    </aside>
  )
}
