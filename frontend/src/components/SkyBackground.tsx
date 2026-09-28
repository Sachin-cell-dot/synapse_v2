import { useEffect, useState, type CSSProperties } from 'react'
import type { WeatherScene } from '../utils/skyState'

interface Props {
  scene: WeatherScene
}

const precipitationParticles = Array.from({ length: 96 }, (_, index) => index)
const windParticles = Array.from({ length: 18 }, (_, index) => index)

/** Distinct density bands so light-rain / rain / storm are visibly different. */
function rainCountForState(state: WeatherScene['state'], intensity: number): number {
  if (state === 'light-rain') return Math.round(18 + intensity * 14)
  if (state === 'rain') return Math.round(38 + intensity * 22)
  if (state === 'storm') return Math.round(64 + intensity * 32)
  return 0
}

function particleStyle(index: number, total: number, dense: boolean): CSSProperties {
  return {
    '--rain-left': `${((index * 37) % 101) - 4}%`,
    '--rain-delay': `${-((index * 0.29) % 2.6)}s`,
    '--rain-duration': `${0.95 + ((index * 0.17) % 0.75)}s`,
    '--rain-length': `${dense ? 22 + ((index * 9) % 34) : 16 + ((index * 7) % 22)}px`,
    '--rain-opacity': `${dense ? 0.62 + ((index % 5) * 0.07) : 0.48 + ((index % 5) * 0.08)}`,
    '--rain-width': dense ? '2.4px' : '1.9px',
    '--rain-total': total,
  } as CSSProperties
}

function StormLightning() {
  const [flashing, setFlashing] = useState(false)

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    let flashTimer = 0
    let resetTimer = 0
    const schedule = () => {
      flashTimer = window.setTimeout(() => {
        setFlashing(true)
        resetTimer = window.setTimeout(() => setFlashing(false), 320)
        schedule()
      }, 4500 + Math.random() * 7500)
    }
    schedule()
    return () => {
      window.clearTimeout(flashTimer)
      window.clearTimeout(resetTimer)
    }
  }, [])

  return <div className={`sky-lightning${flashing ? ' sky-lightning--flash' : ''}`} />
}

function SkyScene({ scene, leaving = false }: { scene: WeatherScene; leaving?: boolean }) {
  const { state, intensity } = scene
  const rainCount = rainCountForState(state, intensity)
  const droplets = precipitationParticles.slice(0, rainCount)
  const dense = state === 'storm' || state === 'rain'
  const sceneStyle = { '--weather-intensity': intensity } as CSSProperties

  return (
    <div className={`sky-scene sky-scene--${state}${leaving ? ' sky-scene--leaving' : ''}`} style={sceneStyle} aria-hidden="true">
      <div className="sky-gradient" />
      <div className="sky-glow" />
      {state === 'clear' && <div className="sky-sun" />}
      {(state === 'cloudy' || droplets.length > 0) && (
        <div className="sky-clouds">
          <i />
          <i />
          <i />
        </div>
      )}
      {droplets.length > 0 && (
        <div className="sky-rain">
          {droplets.map((index) => (
            <i key={index} style={particleStyle(index, droplets.length, dense)} />
          ))}
        </div>
      )}
      {state === 'wind' && (
        <div className="sky-wind">
          {windParticles.map((index) => (
            <i key={index} style={{ '--wind-row': index } as CSSProperties} />
          ))}
        </div>
      )}
      {state === 'storm' && <StormLightning />}
    </div>
  )
}

/** Fixed, CSS-animated backdrop; JS runs only while its selected data state changes. */
export function SkyBackground({ scene }: Props) {
  const [previous, setPrevious] = useState<WeatherScene | null>(null)
  const [current, setCurrent] = useState(scene)

  useEffect(() => {
    if (scene.state === current.state && Math.abs(scene.intensity - current.intensity) < 0.03) return
    setPrevious(current)
    setCurrent(scene)
    const timer = window.setTimeout(() => setPrevious(null), 580)
    return () => window.clearTimeout(timer)
  }, [scene, current])

  return (
    <div className="sky-background" data-sky-state={current.state}>
      {previous && <SkyScene scene={previous} leaving />}
      <SkyScene scene={current} />
    </div>
  )
}
