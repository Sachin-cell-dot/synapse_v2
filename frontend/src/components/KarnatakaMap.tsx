import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { GeoJSON, MapContainer, Marker, Tooltip, ZoomControl, useMap } from 'react-leaflet'
import type { Feature, FeatureCollection, Geometry } from 'geojson'
import { divIcon, geoJSON as leafletGeoJSON, type Layer } from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { DistrictForecast, ForecastSource, ForecastVariable, FutureGfsForecast, RainCategory } from '../types'
import { RAIN_CATEGORIES, categoryForRainfall, shortDate } from '../utils/rainfall'
import { weatherIconForRainfall } from '../utils/weatherIcon'
import { districtInRegion, type KarnatakaRegion } from '../utils/regions'
import { SOURCE_LABELS, VARIABLE_LABELS, unitFor, valueFor } from '../utils/forecastView'
import geoJsonUrl from '../../karnataka_districts.geojson?url'
import indiaGeoJsonUrl from '../../indian.geojson?url'
import worldGeoJsonUrl from '../../world.geojson?url'

interface Props {
  records: DistrictForecast[]
  futureGfsForecast: FutureGfsForecast | null
  selectedDistrict: string | null
  selectedDate: string
  displayHistorical: boolean
  variable: ForecastVariable
  source: ForecastSource
  regionFilter: KarnatakaRegion | 'All'
  expectedDistrictCount: number
  onSelect: (district: string) => void
  onValidation: (state: { loaded: boolean; count: number | null; duplicates: string[]; error?: string }) => void
}

type SynopRow = {
  stationId: string
  latitude: number
  longitude: number
  timestamp: string
  cloudOkta: number | null
  temperatureC: number | null
  dewPointC: number | null
  pressureHpa: number | null
  windSpeedMs: number | null
  windDirectionDeg: number | null
  presentWeatherDescription: string | null
  precipitationAmountMm: number | null
}

type SynopStation = {
  stationId: string
  latitude: number
  longitude: number
  rows: SynopRow[]
}

const NO_DATA_COLOR = '#526273'
const MIN_ZOOM = 4
const MAX_ZOOM = 11
const SYNOP_STATIONS = [
  { stationId: '42182', name: 'New Delhi', latitude: 28.583333, longitude: 77.2 },
  { stationId: '42809', name: 'Kolkata', latitude: 22.65, longitude: 88.45 },
  { stationId: '43003', name: 'Mumbai', latitude: 19.116667, longitude: 72.85 },
  { stationId: '43128', name: 'Hyderabad', latitude: 17.45, longitude: 78.466667 },
  { stationId: '43295', name: 'Bengaluru', latitude: 12.966667, longitude: 77.583333 },
] as const

type DecorativeCloud = { left: number; top: number; width: number; height: number; opacity: number; blur: number; delay: number; speed: number; wobble: number }
const DECORATIVE_CLOUDS: DecorativeCloud[] = Array.from({ length: 32 }, (_, index) => {
  const random = (salt: number) => {
    const value = Math.sin((index + 1) * (salt * 17.31)) * 43758.5453
    return value - Math.floor(value)
  }
  return { left: 2 + random(1) * 94, top: 4 + random(2) * 90, width: 10 + random(3) * 22, height: 5 + random(4) * 11, opacity: 0.1 + random(5) * 0.25, blur: 7 + random(6) * 12, delay: -random(7) * 90, speed: 0.85 + random(8) * 0.3, wobble: -1 + random(9) * 2 }
})

function DecorativeCloudField() {
  const map = useMap()
  const [pane, setPane] = useState<HTMLElement | null>(null)

  useEffect(() => {
    const cloudPane = map.getPane('decorative-clouds') ?? map.createPane('decorative-clouds')
    cloudPane.style.zIndex = '250'
    setPane(cloudPane)
    return () => setPane(null)
  }, [map])

  if (!pane) return null
  const renderClouds = (copy: number) => DECORATIVE_CLOUDS.map((cloud, index) => (
    <div className="decorative-cloud-cluster" key={`${copy}-${index}`} style={{
      '--cloud-left': `${cloud.left + copy * 100}%`, '--cloud-top': `${cloud.top}%`,
      '--cloud-width': `${cloud.width}%`, '--cloud-height': `${cloud.height}%`,
      '--cloud-opacity': cloud.opacity, '--cloud-blur': `${cloud.blur}px`,
      '--cloud-delay': `${cloud.delay}s`, '--cloud-speed': cloud.speed,
      '--cloud-wobble': `${cloud.wobble}deg`,
    } as CSSProperties}>
      <i className="decorative-cloud-blob blob-a" /><i className="decorative-cloud-blob blob-b" />
      <i className="decorative-cloud-blob blob-c" /><i className="decorative-cloud-blob blob-d" />
      <i className="decorative-cloud-blob blob-e" />
    </div>
  ))
  return createPortal(
    <div className="decorative-cloud-viewport" aria-hidden="true">
      <div className="decorative-cloud-field">{renderClouds(0)}{renderClouds(1)}</div>
    </div>, pane,
  )
}

function featureDistrict(feature: Feature<Geometry>): string | null {
  const props = (feature.properties ?? {}) as Record<string, unknown>
  const value = props.district_name ?? props.DISTRICT ?? props.district ?? props.District ?? props.NAME_2 ?? props.NAME
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function districtKey(value: string): string {
  const aliases: Record<string, string> = { bagalkot: 'bagalkote', davanagere: 'davangere', ramnagara: 'ramanagara' }
  const normalized = value.trim().toLowerCase()
  return aliases[normalized] ?? normalized
}

function categoryRange(category: RainCategory): string {
  if (category.min === category.max) return '0 mm'
  if (category.max === null) return `≥${category.min} mm`
  return `${category.min}–${category.max} mm`
}

function localDateFromUtc(dateText: string): string {
  const parsed = new Date(dateText)
  if (Number.isNaN(parsed.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Kolkata',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(parsed)
  const year = parts.find((part) => part.type === 'year')?.value ?? ''
  const month = parts.find((part) => part.type === 'month')?.value ?? ''
  const day = parts.find((part) => part.type === 'day')?.value ?? ''
  return `${year}-${month}-${day}`
}

function splitCsvLine(line: string): string[] {
  const values: string[] = []
  let current = ''
  let inQuotes = false
  for (let index = 0; index < line.length; index += 1) {
    const char = line[index]
    if (char === '"') {
      if (inQuotes && line[index + 1] === '"') {
        current += '"'
        index += 1
      } else {
        inQuotes = !inQuotes
      }
    } else if (char === ',' && !inQuotes) {
      values.push(current)
      current = ''
    } else {
      current += char
    }
  }
  values.push(current)
  return values
}

function parseCsvRows(text: string): Record<string, string>[] {
  const rows: Record<string, string>[] = []
  const lines = text.trim().split(/\r?\n/)
  if (!lines.length) return rows
  const headers = splitCsvLine(lines[0])
  for (let rowIndex = 1; rowIndex < lines.length; rowIndex += 1) {
    const values = splitCsvLine(lines[rowIndex])
    if (!values.length) continue
    const row: Record<string, string> = {}
    headers.forEach((header, index) => {
      row[header] = values[index] ?? ''
    })
    rows.push(row)
  }
  return rows
}

function buildSynopStations(rows: Record<string, string>[]): SynopStation[] {
  const stations = new Map<string, SynopStation>()
  rows.forEach((row) => {
    const stationId = row.station_id?.trim()
    const latitude = Number.parseFloat(row.latitude ?? '')
    const longitude = Number.parseFloat(row.longitude ?? '')
    if (!stationId || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return
    const existing = stations.get(stationId) ?? {
      stationId,
      latitude,
      longitude,
      rows: [],
    }
    const timestamp = row.observation_time_utc ?? ''
    existing.rows.push({
      stationId,
      latitude,
      longitude,
      timestamp,
      cloudOkta: row.total_cloud_amount_oktas ? Number.parseFloat(row.total_cloud_amount_oktas) : null,
      temperatureC: row.air_temperature_c ? Number.parseFloat(row.air_temperature_c) : null,
      dewPointC: row.dew_point_temperature_c ? Number.parseFloat(row.dew_point_temperature_c) : null,
      pressureHpa: row.station_pressure_hpa ? Number.parseFloat(row.station_pressure_hpa) : row.mslp_hpa ? Number.parseFloat(row.mslp_hpa) : null,
      windSpeedMs: row.wind_speed_ms ? Number.parseFloat(row.wind_speed_ms) : null,
      windDirectionDeg: row.wind_direction_deg ? Number.parseFloat(row.wind_direction_deg) : null,
      presentWeatherDescription: row.present_weather_description?.trim() ? row.present_weather_description.trim() : null,
      precipitationAmountMm: row.precipitation_amount_mm ? Number.parseFloat(row.precipitation_amount_mm) : null,
    })
    stations.set(stationId, existing)
  })

  return [...stations.values()]
    .filter((station) => station.rows.length > 0)
    .sort((a, b) => b.rows.length - a.rows.length || a.stationId.localeCompare(b.stationId))
    .slice(0, 5)
}

function observationForDate(station: SynopStation, selectedDate: string): SynopRow | null {
  const matches = station.rows.filter((row) => localDateFromUtc(row.timestamp) === selectedDate)
  if (!matches.length) return null
  return matches.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())[0]
}

function getStationObservation(stationId: string, selectedDate: string, stations: SynopStation[]): SynopRow | null {
  const station = stations.find((item) => item.stationId === stationId)
  if (!station) return null
  const matches = station.rows.filter((row) => localDateFromUtc(row.timestamp) === selectedDate)
  if (!matches.length) return null
  const targetTime = new Date(`${selectedDate}T12:00:00Z`).getTime()
  return matches.reduce((best, candidate) => {
    const bestDistance = Math.abs(new Date(best.timestamp).getTime() - targetTime)
    const candidateDistance = Math.abs(new Date(candidate.timestamp).getTime() - targetTime)
    return candidateDistance < bestDistance ? candidate : best
  }, matches[0] ?? null)
}

function cloudCoverLabel(okta: number | null): string {
  if (okta === null || !Number.isFinite(okta)) return 'No observation'
  if (okta === 0) return 'Clear'
  if (okta <= 2) return 'Few clouds'
  if (okta <= 4) return 'Scattered clouds'
  if (okta <= 7) return 'Broken clouds'
  return 'Overcast'
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function windDriftForObservation(observation: SynopRow | null, stationSeed: number): {
  driftX: number
  driftY: number
  duration: number
  hasRealWind: boolean
} {
  if (
    !observation ||
    observation.windDirectionDeg === null ||
    !Number.isFinite(observation.windDirectionDeg) ||
    observation.windSpeedMs === null ||
    !Number.isFinite(observation.windSpeedMs)
  ) {
    const fallbackX = stationSeed % 2 === 0 ? -8 : 8
    const fallbackY = ((stationSeed % 3) - 1) * 5
    return { driftX: fallbackX, driftY: fallbackY, duration: 20, hasRealWind: false }
  }

  const direction = ((Number(observation.windDirectionDeg) + 180) % 360 + 360) % 360
  const radians = (direction * Math.PI) / 180
  const speed = clamp(Number(observation.windSpeedMs), 0, 25)
  const driftX = Math.sin(radians) * (5 + speed * 0.8)
  const driftY = Math.cos(radians) * (5 + speed * 0.8)
  const duration = clamp(15 - speed * 0.55, 4, 15)

  return { driftX, driftY, duration, hasRealWind: true }
}

function formatNumber(value: number | null, fallback = 'Not available', digits = 1): string {
  if (value === null || !Number.isFinite(value)) return fallback
  return Number(value).toFixed(digits)
}

function formatPressure(value: number | null): string {
  return value === null || !Number.isFinite(value) ? 'Not available' : `${Number(value).toFixed(1)} hPa`
}

function formatWind(value: number | null, direction: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Not available'
  const directionLabel = direction === null || !Number.isFinite(direction) ? 'variable' : `${Math.round(direction)}°`
  return `${Number(value).toFixed(1)} m/s @ ${directionLabel}`
}

/** Fit once to the GeoJSON bounds. Single clicks never zoom. */
function MapViewport({ data }: { data: FeatureCollection }) {
  const map = useMap()

  useEffect(() => {
    const bounds = leafletGeoJSON(data).getBounds()
    if (!bounds.isValid()) return
    map.setMaxBounds(bounds.pad(0.18))
    map.setMinZoom(MIN_ZOOM)
    map.setMaxZoom(MAX_ZOOM)
    map.fitBounds(bounds, { padding: [28, 28], maxZoom: 8, animate: true, duration: 0.45 })
  }, [data, map])

  return null
}

function RainfallLegend() {
  return (
    <div className="map-legend" aria-label="Rainfall category legend">
      <strong>Rainfall (24 h)</strong>
      {RAIN_CATEGORIES.map((category) => (
        <div key={category.label}>
          <i style={{ background: category.color }} />
          <span>{category.label}</span>
          <small>{categoryRange(category)}</small>
        </div>
      ))}
      <div>
        <i style={{ background: NO_DATA_COLOR }} />
        <span>No data</span>
        <small>Unavailable</small>
      </div>
    </div>
  )
}

function AtmosphericLegend({ stations, selectedDate, enabled }: { stations: SynopStation[]; selectedDate: string; enabled: boolean }) {
  const observations = stations.map((station) => getStationObservation(station.stationId, selectedDate, stations))
  const windCount = observations.filter((observation) => observation && Number.isFinite(observation.windDirectionDeg) && Number.isFinite(observation.windSpeedMs)).length
  const cloudCount = observations.filter((observation) => observation && Number.isFinite(observation.cloudOkta)).length
  return (
    <div className="atmospheric-legend" aria-live="polite">
      <strong>Atmospheric flow</strong>
      <span><i className="flow-key" />Direction = SYNOP wind</span>
      <span><i className="cloud-key" />Cloud texture = observed cloud amount</span>
      <small>{enabled ? `${windCount}/5 wind · ${cloudCount}/5 cloud observations` : 'Layer paused'}</small>
    </div>
  )
}

type FlowParticle = { lat: number; lon: number; age: number; life: number; size: number; alpha: number; cloud: boolean }

/**
 * A lightweight canvas renderer. Cloud particles are seeded only at stations
 * with an observed cloud amount; wind is interpolated separately because it
 * is the only field for which a regional flow is defensible here.
 */
function SynopCloudFlow({ stations, selectedDate, enabled }: { stations: SynopStation[]; selectedDate: string; enabled: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const particlesRef = useRef<FlowParticle[]>([])
  const frameRef = useRef<number | null>(null)
  const map = useMap()

  const observations = useMemo(() => stations.map((station) => ({
    station,
    observation: getStationObservation(station.stationId, selectedDate, stations),
  })), [stations, selectedDate])

  useEffect(() => {
    const canvas = canvasRef.current
    const pane = map.getPane('synop-cloud-flow') ?? map.createPane('synop-cloud-flow')
    pane.style.zIndex = '350'
    if (!canvas) return
    pane.appendChild(canvas)
    canvas.className = 'synop-cloud-flow-canvas'
    const context = canvas.getContext('2d')
    if (!context) return
    let width = 0
    let height = 0
    const resize = () => {
      const size = map.getSize()
      const ratio = Math.min(window.devicePixelRatio || 1, 2)
      width = size.x; height = size.y
      canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio)
      canvas.style.width = `${width}px`; canvas.style.height = `${height}px`
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
    }
    resize()

    const validWind = observations.filter(({ observation }) => Boolean(
      observation && Number.isFinite(observation.windDirectionDeg) && Number.isFinite(observation.windSpeedMs),
    ))
    const cloudStations = observations.filter(({ observation }) => Boolean(
      observation && Number.isFinite(observation.cloudOkta),
    ))
    const random = (seed: number) => {
      const value = Math.sin(seed * 12.9898) * 43758.5453
      return value - Math.floor(value)
    }
    const particles: FlowParticle[] = []
    // Flow tracers remain visible even when cloud amount is not reported.
    for (let index = 0; index < 190; index += 1) {
      const source = validWind[index % Math.max(validWind.length, 1)]
      const baseLat = source?.station.latitude ?? 20
      const baseLon = source?.station.longitude ?? 79
      particles.push({ lat: baseLat + (random(index + 3) - 0.5) * 10, lon: baseLon + (random(index + 11) - 0.5) * 14, age: random(index + 19) * 12, life: 14 + random(index + 23) * 12, size: 0.7 + random(index + 29) * 1.8, alpha: 0.12 + random(index + 31) * 0.2, cloud: false })
    }
    // The cloud layer is observation-backed: no cloud amount means no cloud mass.
    cloudStations.forEach(({ station, observation }, stationIndex) => {
      const count = Math.round(18 + (clamp(observation?.cloudOkta ?? 0, 0, 8) / 8) * 42)
      for (let index = 0; index < count; index += 1) {
        const seed = stationIndex * 1000 + index
        particles.push({
          lat: station.latitude + (random(seed + 41) - 0.5) * 2.4,
          lon: station.longitude + (random(seed + 47) - 0.5) * 3.4,
          age: random(seed + 53) * 16,
          life: 18 + random(seed + 59) * 16,
          size: 4 + random(seed + 61) * 12,
          alpha: 0.08 + (clamp(observation?.cloudOkta ?? 0, 0, 8) / 8) * 0.17,
          cloud: true,
        })
      }
    })
    particlesRef.current = particles
    let last = performance.now()
    const move = (now: number) => {
      const seconds = Math.min((now - last) / 1000, 0.08); last = now
      context.clearRect(0, 0, width, height)
      if (enabled && validWind.length) {
        particles.forEach((particle, index) => {
          const field = validWind.reduce((best, item) => {
            const dLat = particle.lat - item.station.latitude
            const dLon = (particle.lon - item.station.longitude) * Math.cos((particle.lat * Math.PI) / 180)
            const distance = Math.max(0.25, Math.hypot(dLat, dLon))
            const weight = 1 / (distance * distance)
            const wind = item.observation!
            const toward = ((wind.windDirectionDeg! + 180) % 360) * Math.PI / 180
            best.weight += weight
            best.u += Math.sin(toward) * wind.windSpeedMs! * weight
            best.v += Math.cos(toward) * wind.windSpeedMs! * weight
            return best
          }, { weight: 0, u: 0, v: 0 })
          const speed = Math.min(25, Math.hypot(field.u, field.v) / Math.max(field.weight, 0.001))
          const u = field.u / Math.max(field.weight, 0.001)
          const v = field.v / Math.max(field.weight, 0.001)
          particle.lon += (u * seconds * 0.012) / Math.max(Math.cos(particle.lat * Math.PI / 180), 0.2)
          particle.lat += v * seconds * 0.012
          particle.age += seconds
          if (particle.age > particle.life || particle.lat < 5 || particle.lat > 35 || particle.lon < 65 || particle.lon > 96) {
            const source = validWind[index % validWind.length].station
            particle.lat = source.latitude + (random(index + Math.floor(now / 1000)) - 0.5) * (particle.cloud ? 2.4 : 10)
            particle.lon = source.longitude + (random(index + 77 + Math.floor(now / 1000)) - 0.5) * (particle.cloud ? 3.4 : 14)
            particle.age = 0
          }
          const point = map.latLngToContainerPoint([particle.lat, particle.lon])
          const fade = Math.min(1, particle.age / 2, (particle.life - particle.age) / 3)
          const alpha = particle.alpha * Math.max(0, fade) * (0.65 + speed / 40)
          context.beginPath()
          context.fillStyle = particle.cloud ? `rgba(205, 232, 245, ${alpha})` : `rgba(124, 214, 244, ${alpha})`
          context.shadowBlur = particle.cloud ? 14 : 4
          context.shadowColor = context.fillStyle
          context.arc(point.x, point.y, particle.size * (particle.cloud ? 1.8 : 1), 0, Math.PI * 2)
          context.fill()
        })
      }
      frameRef.current = requestAnimationFrame(move)
    }
    frameRef.current = requestAnimationFrame(move)
    map.on('resize move zoom', resize)
    return () => { if (frameRef.current) cancelAnimationFrame(frameRef.current); map.off('resize move zoom', resize); canvas.remove() }
  }, [enabled, map, observations])

  return <canvas ref={canvasRef} aria-hidden="true" />
}

export function KarnatakaMap({
  records,
  futureGfsForecast,
  selectedDistrict,
  selectedDate,
  displayHistorical,
  variable,
  source,
  regionFilter,
  expectedDistrictCount,
  onSelect,
  onValidation,
}: Props) {
  const [geoJson, setGeoJson] = useState<FeatureCollection | null>(null)
  const [indiaGeoJson, setIndiaGeoJson] = useState<FeatureCollection | null>(null)
  const [worldGeoJson, setWorldGeoJson] = useState<FeatureCollection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [synopStations, setSynopStations] = useState<SynopStation[]>([])
  const [stationCloudEnabled, setStationCloudEnabled] = useState(false)
  const recordByName = useMemo(
    () => new Map(records.map((record) => [districtKey(record.district), record])),
    [records],
  )
  const mapCenter = useMemo(
    () => (geoJson ? leafletGeoJSON(geoJson).getBounds().getCenter() : undefined),
    [geoJson],
  )

  useEffect(() => {
    fetch(geoJsonUrl)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Supplied GeoJSON was not found (${response.status}).`)
        return response.json() as Promise<FeatureCollection>
      })
      .then((data) => {
        const names = data.features.map(featureDistrict).filter((name): name is string => Boolean(name))
        const duplicates = names.filter((name, index) => names.indexOf(name) !== index)
        if (!data.features.length) throw new Error('GeoJSON has no district features.')
        if (duplicates.length) {
          throw new Error(`GeoJSON has duplicate district names: ${[...new Set(duplicates)].join(', ')}`)
        }
        const coverageError =
          data.features.length === expectedDistrictCount
            ? undefined
            : `Boundary coverage differs from the loaded dataset: ${data.features.length} map boundaries for ${expectedDistrictCount} districts.`
        setGeoJson(data)
        onValidation({ loaded: true, count: data.features.length, duplicates: [], error: coverageError })
      })
      .catch((loadError: unknown) => {
        const message = loadError instanceof Error ? loadError.message : String(loadError)
        setError(message)
        onValidation({ loaded: false, count: null, duplicates: [], error: message })
      })
  }, [expectedDistrictCount, onValidation])

  useEffect(() => {
    fetch(worldGeoJsonUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`World geographic context was not found (${response.status}).`)
        return response.json() as Promise<FeatureCollection>
      })
      .then((data) => setWorldGeoJson(data))
      .catch(() => setWorldGeoJson(null))
  }, [])

  useEffect(() => {
    fetch(indiaGeoJsonUrl)
      .then((response) => {
        if (!response.ok) throw new Error(`India geographic context was not found (${response.status}).`)
        return response.json() as Promise<FeatureCollection>
      })
      .then((data) => setIndiaGeoJson(data))
      .catch(() => setIndiaGeoJson(null))
  }, [])

  useEffect(() => {
    fetch('/decoded_synop.csv')
      .then((response) => {
        if (!response.ok) throw new Error(`SYNOP dataset was not found (${response.status}).`)
        return response.text()
      })
      .then((csvText) => {
        const rows = parseCsvRows(csvText)
        const stations = buildSynopStations(rows)
        setSynopStations(stations)
      })
      .catch(() => setSynopStations([]))
  }, [])

  const style = (feature?: Feature<Geometry>) => {
    const district = feature ? featureDistrict(feature) : null
    const record = district ? recordByName.get(districtKey(district)) : undefined
    const value = valueFor(record, variable, source, displayHistorical)
    const category = variable === 'rainfall' ? categoryForRainfall(value) : null
    const selected = district ? districtKey(district) === districtKey(selectedDistrict ?? '') : false
    const inRegion = district ? districtInRegion(record?.district ?? district, regionFilter) : true
    const fillOpacity = !inRegion ? 0.18 : selected ? 0.94 : category ? 0.8 : 0.46
    return {
      color: selected ? '#f8fbff' : inRegion ? '#d3e3ef' : '#7a8d9c55',
      weight: selected ? 2.8 : inRegion ? 0.9 : 0.5,
      fillColor: category?.color ?? (value !== null ? '#54c5c2' : NO_DATA_COLOR),
      fillOpacity,
      className: 'district-polygon',
    }
  }

  const onEachFeature = (feature: Feature<Geometry>, layer: Layer) => {
    const district = featureDistrict(feature)
    const record = district ? recordByName.get(districtKey(district)) : undefined
    const value = valueFor(record, variable, source, displayHistorical)
    const category = variable === 'rainfall' ? categoryForRainfall(value) : null
    const weatherIcon = weatherIconForRainfall(variable === 'rainfall' ? value : null)
    const label = record?.district ?? district
    layer.on({
      click: (event) => {
        event.originalEvent?.stopPropagation?.()
        if (district) onSelect(label ?? district)
      },
      mouseover: (event) => {
        if (!district) return
        const inRegion = districtInRegion(record?.district ?? district, regionFilter)
        event.target.setStyle({
          weight: 2.4,
          fillOpacity: inRegion ? 0.97 : 0.28,
        })
        event.target.bringToFront()
      },
      mouseout: (event) => event.target.setStyle(style(feature)),
    })
    if (district) {
      layer.bindTooltip(
        `<strong>${weatherIcon.symbol} ${label}</strong><br/>${VARIABLE_LABELS[variable]}: ${value === null ? 'No forecast' : `${value.toFixed(1)} ${unitFor(variable)}`}${
          value === null
            ? '<br/>Coverage: no forecast for this district'
            : `${category ? `<br/>Category: ${category.label}` : ''}<br/>Source: ${displayHistorical ? 'IMD observation' : SOURCE_LABELS[source]}<br/>Target date: ${
                record ? shortDate(record.date) : 'Not available'
              }`
        }`,
        { sticky: true, className: 'district-tooltip', direction: 'top', opacity: 0.96 },
      )
    }
  }

  const title = futureGfsForecast
    ? 'Karnataka map · GFS regional context available'
    : regionFilter === 'All'
      ? `Karnataka map · ${displayHistorical ? 'IMD realised rainfall' : 'rainfall forecast'}`
      : `Karnataka map · ${regionFilter} ${displayHistorical ? 'IMD verification' : 'hindcast'}`

  return (
    <section className="map-card card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Karnataka forecast coverage</p>
          <h2>{displayHistorical ? title : `Karnataka map · ${VARIABLE_LABELS[variable]} · ${SOURCE_LABELS[source]}`}</h2>
        </div>
        <span className="map-status">{geoJson ? `${geoJson.features.length} boundaries` : 'Map validation'}</span>
      </div>
      <div className="map-shell">
        <MapContainer
          center={mapCenter}
          zoom={7}
          minZoom={MIN_ZOOM}
          maxZoom={MAX_ZOOM}
          zoomSnap={1}
          zoomDelta={1}
          wheelPxPerZoomLevel={120}
          maxBoundsViscosity={0.85}
          scrollWheelZoom
          doubleClickZoom={false}
          boxZoom={false}
          touchZoom
          dragging
          zoomAnimation
          fadeAnimation
          zoomControl={false}
          className="leaflet-map"
        >
          <ZoomControl position="bottomright" />
          {geoJson && (
            <>
              <MapViewport data={geoJson} />
              <DecorativeCloudField />
              <SynopCloudFlow stations={synopStations} selectedDate={selectedDate} enabled={stationCloudEnabled} />
              {worldGeoJson && (
                <GeoJSON
                  data={worldGeoJson}
                  interactive={false}
                  style={{ color: '#5f8193', weight: 0.5, opacity: 0.62, fillColor: '#142c3a', fillOpacity: 0.2 }}
                />
              )}
              {indiaGeoJson && (
                <GeoJSON
                  data={indiaGeoJson}
                  interactive={false}
                  style={{ color: '#b8d3e0', weight: 0.72, opacity: 0.72, fillColor: '#27475a', fillOpacity: 0.1 }}
                />
              )}
              <GeoJSON
                key={`geo-${regionFilter}-${displayHistorical ? 'h' : 'f'}-${variable}-${source}-${selectedDistrict ?? 'none'}`}
                data={geoJson}
                style={style}
                onEachFeature={onEachFeature}
              />
              {stationCloudEnabled &&
                SYNOP_STATIONS.map((stationMeta) => {
                  const station = synopStations.find((entry) => entry.stationId === stationMeta.stationId)
                  const observation = station ? getStationObservation(stationMeta.stationId, selectedDate, synopStations) : null
                  const hasCloudObservation = Boolean(
                    observation &&
                      observation.cloudOkta !== null &&
                      Number.isFinite(observation.cloudOkta),
                  )
                  const stationSeed = Number.parseInt(stationMeta.stationId, 10) % 10
                  const plainPointIcon = divIcon({
                    className: 'station-point-marker-shell',
                    html: '<span class="station-point-marker" />',
                    iconSize: [18, 18],
                    iconAnchor: [9, 9],
                    popupAnchor: [0, -10],
                  })

                  if (!hasCloudObservation) {
                    return (
                      <Marker key={stationMeta.stationId} position={[stationMeta.latitude, stationMeta.longitude]} icon={plainPointIcon}>
                        <Tooltip direction="top" offset={[0, -12]} opacity={1} className="synop-tooltip">
                          <div className="synop-tooltip-inner">
                            <strong>{stationMeta.name} · SYNOP station</strong>
                            <div>Station ID: {stationMeta.stationId}</div>
                            <div>Cloud cover: No observation for this date</div>
                            <div>Present weather: Not available</div>
                            <div>Precipitation: Not available</div>
                            <div>Temperature: Not available for this date</div>
                            <div>Wind: Not available for this date</div>
                            <div>Pressure: Not available for this date</div>
                            <div>Station status: No observation for this date</div>
                          </div>
                        </Tooltip>
                      </Marker>
                    )
                  }

                  const cloudObservation = observation!
                  const cloudOkta = cloudObservation.cloudOkta ?? 0
                  const safeOkta = Math.min(8, Math.max(0, cloudOkta))
                  const cloudIntensity = Math.min(0.96, Math.max(0.12, safeOkta / 8))
                  const cloudSpread = 36 + safeOkta * 7
                  const cloudScale = 0.78 + safeOkta * 0.06
                  const drift = windDriftForObservation(cloudObservation, stationSeed)
                  const markerIcon = divIcon({
                    className: 'station-cloud-marker-shell',
                    html: `
                      <div
                        class="station-cloud-marker"
                        style="--cloud-opacity:${cloudIntensity}; --cloud-spread:${cloudSpread}px; --cloud-scale:${cloudScale}; --station-delay:${((stationSeed + 1) * 0.8).toFixed(1)}s; --drift-x:${drift.driftX.toFixed(2)}px; --drift-y:${drift.driftY.toFixed(2)}px; --drift-duration:${drift.duration.toFixed(1)}s;"
                      >
                        <span class="station-cloud-layer station-cloud-layer-1"></span>
                        <span class="station-cloud-layer station-cloud-layer-2"></span>
                        <span class="station-cloud-layer station-cloud-layer-3"></span>
                        <span class="station-cloud-layer station-cloud-layer-4"></span>
                        <span class="station-cloud-layer station-cloud-layer-5"></span>
                        <span class="station-cloud-layer station-cloud-layer-6"></span>
                        <span class="station-cloud-layer station-cloud-layer-7"></span>
                      </div>
                    `,
                    iconSize: [110, 110],
                    iconAnchor: [55, 55],
                    popupAnchor: [0, -24],
                  })

                  return (
                    <Marker key={stationMeta.stationId} position={[stationMeta.latitude, stationMeta.longitude]} icon={markerIcon}>
                      <Tooltip direction="top" offset={[0, -12]} opacity={1} className="synop-tooltip">
                        <div className="synop-tooltip-inner">
                          <strong>{stationMeta.name} · SYNOP station</strong>
                          <div>Station ID: {stationMeta.stationId}</div>
                          <div>Cloud cover: {`${formatNumber(cloudObservation.cloudOkta, 'Not available', 0)} oktas (${cloudCoverLabel(cloudObservation.cloudOkta)})`}</div>
                          <div>Present weather: {cloudObservation.presentWeatherDescription ? cloudObservation.presentWeatherDescription : 'Not available'}</div>
                          <div>Precipitation: {cloudObservation.precipitationAmountMm !== null && cloudObservation.precipitationAmountMm !== undefined ? `${Number(cloudObservation.precipitationAmountMm || 0).toFixed(1)} mm` : 'Not available'}</div>
                          <div>Temperature: {`${formatNumber(cloudObservation.temperatureC, 'Not available')}°C`}</div>
                          <div>Wind: {formatWind(cloudObservation.windSpeedMs, cloudObservation.windDirectionDeg)}</div>
                          <div>Pressure: {formatPressure(cloudObservation.pressureHpa)}</div>
                          <div>Station status: Point observation only, not regional coverage</div>
                        </div>
                      </Tooltip>
                    </Marker>
                  )
                })}
              {variable === 'rainfall' ? <RainfallLegend /> : (
                <div className="map-legend">
                  <strong>{VARIABLE_LABELS[variable]}</strong>
                  <div><i style={{ background: '#54c5c2' }} /><span>Forecast available</span><small>{unitFor(variable)}</small></div>
                  <div><i style={{ background: NO_DATA_COLOR }} /><span>No forecast</span><small>Unavailable</small></div>
                </div>
              )}
              <AtmosphericLegend stations={synopStations} selectedDate={selectedDate} enabled={stationCloudEnabled} />
            </>
          )}
        </MapContainer>
        <span className="decorative-cloud-label">Decorative atmosphere — not a live cloud observation</span>
        <div className="map-layer-control" aria-live="polite">
          <button
            type="button"
            className={`map-toggle-button ${stationCloudEnabled ? 'enabled' : ''}`}
            onClick={() => setStationCloudEnabled((enabled) => !enabled)}
            aria-pressed={stationCloudEnabled}
          >
            <span className="toggle-label">Cloud Flow: SYNOP</span>
            <span className="toggle-state">{stationCloudEnabled ? 'On' : 'Off'}</span>
          </button>
          <span className="layer-status-text">
            {stationCloudEnabled
              ? 'Animated regional SYNOP-derived atmospheric flow · 5 stations'
              : 'Regional SYNOP atmospheric context · flow layer off'}
          </span>
        </div>
        {!geoJson && (
          <div className="map-unavailable">
            <span>Map data unavailable</span>
            <p>{error ?? 'Loading Karnataka district GeoJSON…'}</p>
            <small>No district polygons are drawn or approximated.</small>
          </div>
        )}
      </div>
      {geoJson && geoJson.features.length !== expectedDistrictCount && (
        <p className="map-coverage-note">
          {futureGfsForecast
            ? `The real GFS value is regional context from ${futureGfsForecast.gridCellCount} cells, not a district-local map value; district polygons remain uncoloured until district forecasts are supplied.`
            : `India state boundaries provide geographic context. Rainfall values are supplied only for Karnataka districts; other states are intentionally not coloured. ${expectedDistrictCount - geoJson.features.length} loaded district boundary is unavailable in the supplied map file.`}
        </p>
      )}
    </section>
  )
}
