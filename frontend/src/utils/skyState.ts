import type { DistrictForecast, NullableNumber } from '../types'
import { categoryForRainfall } from './rainfall'

/** Presentation-only sky states derived from the same rainfall value shown in the UI. */
export type SkyState = 'clear' | 'cloudy' | 'wind' | 'light-rain' | 'rain' | 'storm'

export interface WeatherScene {
  state: SkyState
  intensity: number
  label: string
  source: 'selected district' | 'statewide average' | 'no weather data'
}

export type RainfallDisplayMode = 'forecast' | 'historical'

export function rainfallForSky(record: DistrictForecast, mode: RainfallDisplayMode): NullableNumber {
  return mode === 'historical' ? record.actualRainfall : record.finalForecast ?? record.gfs
}

export function skyStateForRainfall(value: NullableNumber): SkyState {
  const category = categoryForRainfall(value)?.label

  switch (category) {
    case 'Very light':
    case 'Light':
      return 'light-rain'
    case 'Moderate':
      return 'rain'
    case 'Rather heavy':
    case 'Heavy':
    case 'Very Heavy':
    case 'Extremely Heavy':
      return 'storm'
    case 'Dry':
    default:
      // Missing, invalid, and uncategorised values deliberately make no weather claim.
      return 'clear'
  }
}

export function skyStateForDistrict(record: DistrictForecast | null, mode: RainfallDisplayMode): SkyState {
  return record ? skyStateForRainfall(rainfallForSky(record, mode)) : 'clear'
}

function intensityForRainfall(value: NullableNumber): number {
  if (value === null || !Number.isFinite(value) || value <= 0) return 0
  const category = categoryForRainfall(value)
  if (!category) return 0
  const upper = category.max ?? Math.max(category.min * 1.7, value)
  const withinCategory = upper === category.min ? 1 : (value - category.min) / (upper - category.min)
  return Math.min(1, Math.max(0.12, (RAIN_INTENSITY_BASE[category.label] ?? 0) + withinCategory * 0.16))
}

const RAIN_INTENSITY_BASE: Record<string, number> = {
  Dry: 0,
  'Very light': 0.12,
  Light: 0.25,
  Moderate: 0.43,
  'Rather heavy': 0.61,
  Heavy: 0.76,
  'Very Heavy': 0.88,
  'Extremely Heavy': 0.96,
}

/** A presentation descriptor. It reads forecast values but never mutates or calls forecasting code. */
export function weatherSceneForRainfall(
  value: NullableNumber,
  source: WeatherScene['source'],
  windSpeedKmh: NullableNumber = null,
): WeatherScene {
  const category = categoryForRainfall(value)
  const hasRain = value !== null && Number.isFinite(value) && value > 0
  const state = !hasRain && windSpeedKmh !== null && windSpeedKmh >= 25 ? 'wind' : skyStateForRainfall(value)
  return {
    state,
    intensity: state === 'wind' ? Math.min(1, Math.max(0.25, (windSpeedKmh ?? 0) / 70)) : intensityForRainfall(value),
    label: state === 'wind' ? 'Windy' : category?.label ?? 'Weather unavailable',
    source,
  }
}
