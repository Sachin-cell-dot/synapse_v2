import type { NullableNumber } from '../types'
import { categoryForRainfall } from './rainfall'

export interface WeatherIcon { symbol:string; label:string }

const ICONS:Record<string,WeatherIcon>={
  Dry:{symbol:'☀',label:'Dry'},
  'Very light':{symbol:'☁',label:'Very light rain'},
  Light:{symbol:'☂',label:'Light rain'},
  Moderate:{symbol:'☂',label:'Moderate rain'},
  'Rather heavy':{symbol:'☔',label:'Rather heavy rain'},
  Heavy:{symbol:'☔',label:'Heavy rain'},
  'Very Heavy':{symbol:'⚡',label:'Very heavy rain'},
  'Extremely Heavy':{symbol:'⚡',label:'Extremely heavy rain'},
}

const NO_DATA_ICON:WeatherIcon={symbol:'—',label:'No rainfall data'}
export function weatherIconForRainfall(value:NullableNumber):WeatherIcon {
  const category=categoryForRainfall(value)
  return category?ICONS[category.label]??NO_DATA_ICON:NO_DATA_ICON
}
