import type { DistrictForecast } from '../types'
import { displayRainfall, shortDate } from '../utils/rainfall'

export function RainfallChart({ records }: { records: DistrictForecast[] }) {
  const points = records.slice(-7)
  if (!points.length) return <p className="empty-copy">No historical chart data is available for this district.</p>
  const values = points.flatMap((point) => [point.finalForecast, point.actualRainfall]).filter((value): value is number => value !== null)
  const maximum = Math.max(...values, 1)
  return <div className="rain-chart" aria-label="SYNAPSE-WX hindcast and IMD verification chart">
    <div className="chart-key"><span><i className="key-gfs" />SYNAPSE-WX</span><span><i className="key-imd" />IMD verification</span></div>
    <div className="bar-series">{points.map((point) => <div className="chart-group" key={point.date}>
      <div className="bar-pair"><span className="bar gfs" style={{ height: `${((point.finalForecast ?? 0) / maximum) * 100}%` }} title={`SYNAPSE-WX ${displayRainfall(point.finalForecast)}`} /><span className="bar imd" style={{ height: `${((point.actualRainfall ?? 0) / maximum) * 100}%` }} title={`IMD verification ${displayRainfall(point.actualRainfall)}`} /></div>
      <small>{shortDate(point.date).slice(0, 6)}</small>
    </div>)}</div>
  </div>
}
