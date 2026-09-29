import { useCallback, useEffect, useMemo, useState } from 'react'
import { loadForecastStore } from './data/adapter'
import { KarnatakaMap } from './components/KarnatakaMap'
import { DistrictPanel } from './components/DistrictPanel'
import { SkyBackground } from './components/SkyBackground'
import type { DashboardMode, DataStatus, DistrictForecast, ForecastSource, ForecastStore, ForecastVariable } from './types'
import { RAIN_CATEGORIES, categoryCounts, displayRainfall, shortDate } from './utils/rainfall'
import { DISTRICT_REGION, REGIONS, districtInRegion, type KarnatakaRegion } from './utils/regions'
import { rainfallForSky, weatherSceneForRainfall } from './utils/skyState'
import { weatherIconForRainfall } from './utils/weatherIcon'
import { HELD_OUT_COMPARISON, SOURCE_LABELS, VARIABLE_LABELS, datesForMode, recordsForSelection, synopPairSummary, thresholdCoverage, unitFor, valueFor } from './utils/forecastView'

const initialStore: ForecastStore = {
  records: [],
  futureGfsForecasts: [],
  dates: [],
  districts: [],
  verificationMetrics: [],
  synopStations: [],
  synopVerificationMetrics: [],
  status: {
    masterLoaded: false,
    errorOutputLoaded: false,
    geoJsonLoaded: false,
    geoDistrictCount: null,
    duplicateGeoDistrictNames: [],
    errors: [],
  },
}

function recordFor(records: DistrictForecast[], date: string, district: string | null) {
  return records.find((record) => record.date === date && record.district === district) ?? null
}

function issueTimeLabel(value: string | null): string {
  if (!value) return 'issuance time unavailable'
  return new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)) + ' IST'
}

export default function App() {
  const [store, setStore] = useState(initialStore)
  const [loading, setLoading] = useState(true)
  const [mode, setMode] = useState<DashboardMode>('operational')
  const [date, setDate] = useState('')
  const [district, setDistrict] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [region, setRegion] = useState<KarnatakaRegion | 'All'>('All')
  const [lead, setLead] = useState(1)
  const [variable, setVariable] = useState<ForecastVariable>('rainfall')
  const [source, setSource] = useState<ForecastSource>('blend')
  const [rainThreshold, setRainThreshold] = useState(50)
  const [heatThreshold, setHeatThreshold] = useState(40)
  const [windThreshold, setWindThreshold] = useState(12)
  const [aboutOpen, setAboutOpen] = useState(false)
  const [devOpen, setDevOpen] = useState(false)
  const [geoState, setGeoState] = useState<
    Pick<DataStatus, 'geoJsonLoaded' | 'geoDistrictCount' | 'duplicateGeoDistrictNames' | 'errors'>
  >({ geoJsonLoaded: false, geoDistrictCount: null, duplicateGeoDistrictNames: [], errors: [] })
  const showDeveloperStatus = new URLSearchParams(window.location.search).get('dataStatus') === 'true'

  useEffect(() => {
    loadForecastStore()
      .then((loaded) => {
        setStore(loaded)
        const today = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' })
          .formatToParts(new Date())
          .reduce<Record<string, string>>((parts, item) => ({ ...parts, [item.type]: item.value }), {})
        const localToday = `${today.year}-${today.month}-${today.day}`
        const dayOne = loaded.records.find((item) => item.dataMode === 'operational_forecast' && item.leadDays === 1)
        setDate(dayOne?.date ?? (loaded.dates.includes(localToday) ? localToday : loaded.dates.at(-1) ?? ''))
        setLead(dayOne?.leadDays ?? 1)
        setDistrict(dayOne?.district ?? loaded.districts[0] ?? null)
      })
      .finally(() => setLoading(false))
  }, [])

  const availableDates = useMemo(() => datesForMode(store.records, mode), [store.records, mode])
  const selectedRecords = useMemo(() => recordsForSelection(store.records, mode, date, lead), [store.records, mode, date, lead])
  const regionRecords = useMemo(
    () => selectedRecords.filter((record) => districtInRegion(record.district, region)),
    [selectedRecords, region],
  )
  const record = useMemo(() => selectedRecords.find((item) => item.district === district) ?? null, [selectedRecords, district])
  const futureGfsForecast = useMemo(
    () => store.futureGfsForecasts.find((forecast) => forecast.date === date) ?? null,
    [store.futureGfsForecasts, date],
  )
  const isOperationalForecast = selectedRecords.some((item) => item.dataMode === 'operational_forecast')
  const operationalLead = selectedRecords.find((item) => item.dataMode === 'operational_forecast')?.leadDays ?? null
  const issueTime = selectedRecords.find((item) => item.issueTime)?.issueTime ?? null
  const history = useMemo(
    () => store.records.filter((item) => item.dataMode === 'historical_proxy' && item.district === district && item.date <= date),
    [store.records, district, date],
  )
  const visibleValue = (item: DistrictForecast) => valueFor(item, variable, source, false)
  const populated = regionRecords.map((item) => ({ value: visibleValue(item) })).filter((item) => item.value !== null)
  const average = populated.length
    ? populated.reduce((sum, item) => sum + (item.value ?? 0), 0) / populated.length
    : null
  const statewideValues = selectedRecords
    .map((item) => visibleValue(item))
    .filter((value): value is number => value !== null && Number.isFinite(value))
  const statewideAverage = statewideValues.length
    ? statewideValues.reduce((sum, value) => sum + value, 0) / statewideValues.length
    : null
  const sceneValue = district && record ? rainfallForSky(record, 'forecast') : statewideAverage
  const weatherScene = weatherSceneForRainfall(
    sceneValue,
    district && record ? 'selected district' : statewideValues.length ? 'statewide average' : 'no weather data',
  )
  const skyState = weatherScene.state
  const counts = categoryCounts(regionRecords.map((item) => ({ value: visibleValue(item) })))
  const matches = store.districts
    .filter((name) => districtInRegion(name, region))
    .filter((name) => {
      const query = search.trim().toLowerCase().replaceAll('ramnagara', 'ramanagara')
      return name.toLowerCase().includes(query)
    })
    .slice(0, 6)
  const searchIcon = (name: string) => {
    const matchingRecord = recordFor(store.records, date, name)
    return weatherIconForRainfall(matchingRecord ? visibleValue(matchingRecord) : null).symbol
  }
  const unavailable = Boolean(date) && !selectedRecords.length && !futureGfsForecast
  const heavyCount =
    (counts.get('Heavy') ?? 0) + (counts.get('Very Heavy') ?? 0) + (counts.get('Extremely Heavy') ?? 0)
  const operationalLeads = [...new Set(store.records.filter((item) => item.dataMode === 'operational_forecast').map((item) => item.leadDays).filter((item): item is number => item !== null))].sort((a, b) => a - b)
  const guidance = {
    rainfall: thresholdCoverage(selectedRecords, 'rainfall', rainThreshold, store.districts.length),
    heat: thresholdCoverage(selectedRecords, 'tmax', heatThreshold, store.districts.length),
    wind: thresholdCoverage(selectedRecords, 'wind_max', windThreshold, store.districts.length),
  }
  const synopStations = store.synopStations ?? []
  const synopVerificationMetrics = store.synopVerificationMetrics ?? []
  const synopSummary = synopPairSummary(synopVerificationMetrics)

  const updateGeo = useCallback(
    (state: { loaded: boolean; count: number | null; duplicates: string[]; error?: string }) => {
      setGeoState({
        geoJsonLoaded: state.loaded,
        geoDistrictCount: state.count,
        duplicateGeoDistrictNames: state.duplicates,
        errors: state.error ? [state.error] : [],
      })
    },
    [],
  )

  if (loading) {
    return (
      <><SkyBackground scene={weatherSceneForRainfall(null, 'no weather data')} /><main className="loading-screen"><div className="loading-orb" /><p>Loading verified SYNAPSE-WX datasets…</p></main></>
    )
  }
  if (!store.records.length) {
    return (
      <><SkyBackground scene={weatherSceneForRainfall(null, 'no weather data')} /><main className="loading-screen"><h1>SYNAPSE-WX</h1><p>Forecast data unavailable.</p><small>{store.status.errors.join(' · ')}</small></main></>
    )
  }

  return (
    <>
      <SkyBackground scene={weatherScene} />
      <main className="app-shell" data-sky-state={skyState}>
      <header className="topbar glass-surface">
        <div className="brand">
          <span className="brand-mark">S</span>
          <div>
            <h1>SYNAPSE-WX</h1>
            <p>Adaptive Three-Model Rainfall Forecasting</p>
          </div>
        </div>
        <div className="header-controls">
          <label className="control date-control">
            <span>{mode === 'historical' ? 'Evaluation date' : 'Valid date'}</span>
            <select value={date} onChange={(event) => { const next = event.target.value; setDate(next); const match = store.records.find((item) => item.date === next && item.dataMode === 'operational_forecast'); if (match?.leadDays) setLead(match.leadDays) }}>
              {availableDates.map((available) => (
                <option value={available} key={available}>
                  {shortDate(available)}
                </option>
              ))}
            </select>
          </label>
          <label className="control">
            <span>Lead</span>
            <select value={lead} onChange={(event) => { const next = Number(event.target.value); setLead(next); const match = store.records.find((item) => item.dataMode === 'operational_forecast' && item.leadDays === next); if (match) setDate(match.date) }}>
              {operationalLeads.map((value) => <option value={value} key={value}>Day {value}</option>)}
            </select>
          </label>
          <label className="control">
            <span>Variable</span>
            <select value={variable} onChange={(event) => { const next = event.target.value as ForecastVariable; setVariable(next); if (next !== 'rainfall') { setMode('operational'); const dates = datesForMode(store.records, 'operational'); if (!dates.includes(date)) setDate(dates[0] ?? ''); if (source.startsWith('gefs')) setSource('blend') } }}>
              <option value="rainfall">Rainfall</option><option value="tmax">Daily Tmax</option><option value="tmin">Daily Tmin</option><option value="wind_max">Maximum wind</option>
            </select>
          </label>
          <label className="control">
            <span>Source / blend</span>
            <select value={source} onChange={(event) => setSource(event.target.value as ForecastSource)}>
              <option value="blend">SYNAPSE-WX blend</option><option value="gfs">GFS</option><option value="ifs_hres">IFS HRES</option><option value="aifs">AIFS</option>
              {variable === 'rainfall' && <><option value="gefs_mean">GEFS mean · display only</option><option value="gefs_spread">GEFS spread · display only</option></>}
            </select>
          </label>
          <label className="control region-control">
            <span>Region</span>
            <select
              value={region}
              onChange={(event) => setRegion(event.target.value as KarnatakaRegion | 'All')}
              aria-label="Filter by Karnataka region"
            >
              <option value="All">All Karnataka</option>
              {REGIONS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          <label className="control district-control">
            <span>District</span>
            <input
              placeholder="Search district"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onFocus={() => setSearch((current) => current || '')}
            />
          </label>
          {search && (
            <div className="search-results">
              {matches.map((name) => (
                <button
                  key={name}
                  onClick={() => {
                    setDistrict(name)
                    setSearch('')
                  }}
                >
                  <span className="search-weather-icon" aria-hidden="true">{searchIcon(name)}</span>
                  {name}
                </button>
              ))}
              {!matches.length && <span>No matching district</span>}
            </div>
          )}
          <div className="mode-toggle" aria-label="Dataset mode">
            <button className={mode === 'historical' ? 'active' : ''} onClick={() => { const dates = datesForMode(store.records, 'historical'); setMode('historical'); setVariable('rainfall'); setSource('blend'); setDate(dates.at(-1) ?? '') }}>
              Historical evaluation
            </button>
            <button className={mode === 'operational' ? 'active' : ''} onClick={() => { const dates = datesForMode(store.records, 'operational'); setMode('operational'); setDate(dates[0] ?? '') }}>
              Operational forecast
            </button>
          </div>
          <span className="lead-badge">24H</span>
        </div>
      </header>

      <section className="date-line">
        <span className={mode === 'operational' ? 'forecast-pill' : 'history-pill'}>
          {mode === 'operational' ? `OPERATIONAL FORECAST · DAY-${operationalLead ?? '—'}` : 'HISTORICAL PROXY EVALUATION'}
        </span>
        <strong>{shortDate(date)}</strong>
        {region !== 'All' && <span className="region-pill">{region}</span>}
        <span>
          {mode === 'operational'
            ? `Valid ${shortDate(date)} · issued ${issueTimeLabel(issueTime)} · Verification pending`
            : 'Matched forecast–IMD rows · exact upstream run identity unverified · not a past operational issuance'}
        </span>
        <span className="atmosphere-pill"><i /> {weatherScene.label} atmosphere · {weatherScene.source}</span>
      </section>
      {unavailable && <div className="availability-alert">Forecast data unavailable for this date.</div>}

      <section className="summary-grid">
        <article className="summary-card identity anim-card" style={{ animationDelay: '40ms' }}>
          <p className="eyebrow">{region === 'All' ? 'Karnataka administrative coverage' : region}</p>
          <strong>
            {region === 'All' ? store.districts.length : Object.keys(DISTRICT_REGION).filter((name) => store.districts.includes(name) && DISTRICT_REGION[name] === region).length}{' '}
            <small>administrative districts</small>
          </strong>
          <span>{futureGfsForecast ? `${regionRecords.length} districts with district-level forecast data` : `${regionRecords.length} with data on this date`}</span>
        </article>
        <article className="summary-card anim-card" style={{ animationDelay: '90ms' }}>
          <p className="eyebrow">{VARIABLE_LABELS[variable]} · {SOURCE_LABELS[source]}</p>
          <strong>{average === null ? 'No forecast' : `${average.toFixed(1)} ${unitFor(variable)}`}</strong>
          <span>{populated.length} of {store.districts.length} districts with available values</span>
        </article>
        <article className="summary-card anim-card" style={{ animationDelay: '140ms' }}>
          <p className="eyebrow">District forecast coverage</p>
          <strong>{populated.length}<small> / {store.districts.length}</small></strong>
          <span>Absent districts are explicitly shown as no forecast</span>
        </article>
        <article className="summary-card alert-card anim-card" style={{ animationDelay: '190ms' }}>
          <p className="eyebrow">{futureGfsForecast ? 'District verification' : 'Heavy rainfall districts'}</p>
          <strong>{futureGfsForecast ? '—' : heavyCount}</strong>
          <span>
            {futureGfsForecast ? 'Not available yet' : `Heavy+ on ${shortDate(date)}`}
            {region !== 'All' ? ` · ${region}` : ''}
          </span>
        </article>
      </section>

      <section className="dashboard-grid">
        <KarnatakaMap
          records={selectedRecords}
          selectedDistrict={district}
          selectedDate={date}
          displayHistorical={false}
          variable={variable}
          source={source}
          futureGfsForecast={futureGfsForecast}
          regionFilter={region}
          expectedDistrictCount={store.districts.length}
          onSelect={setDistrict}
          onValidation={updateGeo}
        />
        <DistrictPanel
          record={record}
          history={history}
          historical={mode === 'historical'}
          futureGfsForecast={futureGfsForecast}
          districtName={district}
          variable={variable}
          source={source}
          onClose={() => setDistrict(null)}
        />
      </section>

      <section className="guidance-grid">
        <article className="card guidance-card">
          <p className="eyebrow">Experimental threshold guidance</p>
          <h2>Target period · {shortDate(date)} · Day-{operationalLead ?? lead}</h2>
          <div className="threshold-grid">
            <label>Rainfall threshold <input type="number" min="0" value={rainThreshold} onChange={(event) => setRainThreshold(Number(event.target.value))} /> mm</label>
            <span>{guidance.rainfall.exceedances} exceedance · coverage {guidance.rainfall.available}/{guidance.rainfall.expected}</span>
            <label>Heat-risk screening <input type="number" value={heatThreshold} onChange={(event) => setHeatThreshold(Number(event.target.value))} /> °C Tmax</label>
            <span>{guidance.heat.exceedances} exceedance · coverage {guidance.heat.available}/{guidance.heat.expected}</span>
            <label>High-wind threshold <input type="number" min="0" value={windThreshold} onChange={(event) => setWindThreshold(Number(event.target.value))} /> m/s</label>
            <span>{guidance.wind.exceedances} exceedance · coverage {guidance.wind.available}/{guidance.wind.expected}</span>
          </div>
          <p className="overview-note">Experimental screening only. The temperature threshold is not an official heat-wave warning and does not implement verified duration or agency criteria.</p>
        </article>
        <article className="card verification-scorecard">
          <p className="eyebrow">Frozen held-out rainfall verification</p>
          <h2>May–August 2026 · 3,749 paired district-days</h2>
          <div className="scorecard-table"><span>Method</span><span>MAE</span><span>RMSE</span><span>Bias</span><span>50 mm CSI</span>
            {store.verificationMetrics.map((metric) => <div className="scorecard-row" key={metric.method}><b>{metric.method.replaceAll('_', ' ')}</b><span>{metric.mae.toFixed(3)}</span><span>{metric.rmse.toFixed(3)}</span><span>{metric.bias.toFixed(3)}</span><span>{metric.heavyRainCsi?.toFixed(3) ?? 'N/A'}</span></div>)}
          </div>
          <p className="overview-note">{HELD_OUT_COMPARISON} Historical observations appear only in this verification view, never in future forecast cards.</p>
        </article>
      </section>

      <section className="overview-section card synop-verification">
        <div className="section-heading"><div><p className="eyebrow">SYNOP point observations</p><h2>Station coverage and point verification</h2></div><span>{synopStations.length} stations</span></div>
        <p className="overview-note">These are point observations, not 31-district ground truth. Forecasts must match the station coordinate and valid time; district forecasts are never substituted.</p>
        <div className="scorecard-table"><span>Station / location</span><span>Observations</span><span>Coverage</span><span>Verification</span><span>Status</span>
          {synopStations.map((station) => {
            const metrics = synopVerificationMetrics.filter((metric) => metric.stationId === station.stationId)
            return <div className="scorecard-row" key={station.stationId}><b>{station.stationId} · {station.latitude.toFixed(3)}, {station.longitude.toFixed(3)}</b><span>{station.observationCount}</span><span>{station.firstObservationUtc.slice(0, 10)}–{station.lastObservationUtc.slice(0, 10)}</span><span>{metrics.length ? `${synopSummary.fullMatchedPairs.toLocaleString('en-IN')} full-availability pairs` : 'No matched pairs'}</span><span>{metrics.length ? `Common: temperature ${synopSummary.commonTemperaturePairs.toLocaleString('en-IN')} · wind ${synopSummary.commonWindPairs.toLocaleString('en-IN')}` : 'Unavailable: no exact station forecast'}</span></div>
          })}
        </div>
        {synopVerificationMetrics.length > 0 && <><div className="scorecard-table"><span>Scope · station · variable · source</span><span>N / days</span><span>MAE</span><span>RMSE</span><span>Bias</span>{synopVerificationMetrics.map((metric) => <div className="scorecard-row" key={`${metric.scope}-${metric.stationId}-${metric.variableId}-${metric.sourceId}-${metric.leadHours}`}><b>{metric.scope.replaceAll('_', ' ')} · {metric.stationId} · {metric.variableId} · {metric.sourceId}</b><span>{metric.n} / {metric.days}</span><span>{metric.mae.toFixed(2)}</span><span>{metric.rmse.toFixed(2)}</span><span>{metric.bias.toFixed(2)}</span></div>)}</div><p className="overview-note">Historical issue time: unverified exact run (`previous_day1` product). Wind-height comparison: SYNOP sensor height is undocumented; forecasts are 10 m wind.</p></>}
      </section>

      <section className="overview-section card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{futureGfsForecast ? 'District data status' : 'Rainfall legend & counts'}</p>
            <h2>
              {futureGfsForecast ? 'District forecast not available yet' : `${mode === 'historical' ? 'Historical proxy forecast' : 'Forecast'} distribution`}
              {region !== 'All' ? ` · ${region}` : ''}
            </h2>
          </div>
          <span>{shortDate(date)}</span>
        </div>
        <div className="rainfall-overview">
          {RAIN_CATEGORIES.map((category) => (
            <div className="category-row" key={category.label}>
              <span className="category-dot" style={{ background: category.color }} />
              <b>{category.label}</b>
              <div className="track">
                <i
                  style={{
                    width: `${((counts.get(category.label) ?? 0) / Math.max(regionRecords.length, 1)) * 100}%`,
                    background: category.color,
                  }}
                />
              </div>
              <strong>{counts.get(category.label) ?? 0}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="secondary-panel card">
        <button
          type="button"
          className="collapse-toggle"
          aria-expanded={aboutOpen}
          onClick={() => setAboutOpen((open) => !open)}
        >
          <span>About SYNAPSE-WX · models &amp; context</span>
          <strong>{aboutOpen ? 'Hide' : 'Show'}</strong>
        </button>
        {aboutOpen && (
          <div className="collapse-body">
            <p>
              SYNAPSE-WX combines saved GFS, IFS HRES, and AIFS rainfall hindcasts using district-specific adaptive
              trust weights derived only from earlier IMD errors. This is a historical evaluation, not an official warning.
            </p>
            <div className="architecture">
              <span>GFS</span>
              <i>+</i>
              <span>IFS HRES</span>
              <i>+</i>
              <span>AIFS</span>
              <i>+</i>
              <span>Adaptive trust</span>
              <i>↓</i>
              <b>60-day inverse-MAE blend</b>
              <i>↓</i>
              <strong>SYNAPSE-WX 24-hour hindcast</strong>
            </div>
            <p className="overview-note">
              Weights use only prior district-date errors and sum to 100%. Heavy districts on this date: {heavyCount}.
            </p>
          </div>
        )}
      </section>

      {showDeveloperStatus && (
        <section className="developer-status card">
          <button
            type="button"
            className="collapse-toggle"
            aria-expanded={devOpen}
            onClick={() => setDevOpen((open) => !open)}
          >
            <span>Developer data status</span>
            <strong>{devOpen ? 'Hide' : 'Show'}</strong>
          </button>
          {devOpen && (
            <div className="collapse-body">
              <div>
                <span>Master CSV: {store.status.masterLoaded ? 'loaded' : 'missing'}</span>
                <span>Error-model output: {store.status.errorOutputLoaded ? 'loaded' : 'missing'}</span>
                <span>
                  GeoJSON:{' '}
                  {geoState.geoJsonLoaded ? `${geoState.geoDistrictCount} validated districts` : 'not available'}
                </span>
              </div>
              {[...store.status.errors, ...geoState.errors].map((error) => (
                <small key={error}>{error}</small>
              ))}
            </div>
          )}
        </section>
      )}

      <footer>
        <h2>Data &amp; Model Transparency</h2>
        <p>
          Forecast outputs are loaded from the frozen SYNAPSE-WX hindcast. IMD rainfall
          represents realised observations and must not be interpreted as forecasts. Model components are shown
          only when corresponding data are available. Region filters are geographic metadata only.
        </p>
        <span>{isOperationalForecast ? `Operational / Day-${operationalLead ?? '—'}` : 'Historical proxy evaluation'}</span>
      </footer>
      </main>
    </>
  )
}
