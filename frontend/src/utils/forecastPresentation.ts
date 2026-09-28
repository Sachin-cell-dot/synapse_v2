import type { NullableNumber } from '../types'
import { displayRainfall } from './rainfall'

const NA = 'Not available yet'
export function formatExpectedError(value: NullableNumber): string {
  return value === null || !Number.isFinite(value) ? NA : displayRainfall(value)
}

export function formatTrust(value: NullableNumber): string {
  return value === null || !Number.isFinite(value) ? NA : `${(value * 100).toFixed(1)}%`
}
