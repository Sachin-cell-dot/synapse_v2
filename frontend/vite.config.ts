import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
// @ts-expect-error Node declarations are intentionally not a browser dependency.
import { readdirSync, readFileSync, statSync } from 'node:fs'

const operationalExportDirectory = new URL('../outputs/operational/exports/', import.meta.url)
const operationalConfigPath = new URL('../configs/operational.example.json', import.meta.url)
const operationalRoute = '/synapse_wx_latest_operational_cycle.csv'

function combinedOperationalCycles(): string | null {
  try {
    const configuration = JSON.parse(readFileSync(operationalConfigPath, 'utf8')) as { project: { mode: string }; archive: { reconstruction_mode: string } }
    const candidates: Array<{ header: string; rows: string[]; mode: string; issued: string; modified: number }> = readdirSync(operationalExportDirectory)
      .filter((name: string) => /^synapse_wx_cycle_[a-f0-9]+\.csv$/i.test(name))
      .map((name: string) => {
        const path = new URL(name, operationalExportDirectory)
        const lines = readFileSync(path, 'utf8').trim().split(/\r?\n/)
        const columns = lines[0].split(',')
        const first = lines[1]?.split(',') ?? []
        return { header: lines[0], rows: lines.slice(1), mode: first[columns.indexOf('mode')] ?? '', issued: first[columns.indexOf('issued_at_utc')] ?? '', modified: statSync(path).mtimeMs }
      })
    const live = candidates.filter((item) => item.mode === configuration.project.mode).sort((a, b) => b.issued.localeCompare(a.issued) || b.modified - a.modified)[0]
    const reconstructed = candidates.filter((item) => item.mode === configuration.archive.reconstruction_mode).flatMap((item) => item.rows)
    if (!live && !reconstructed.length) return null
    const header = live?.header ?? candidates.find((item) => item.rows.length)?.header
    return header ? `${header}\n${[...reconstructed, ...(live?.rows ?? [])].join('\n')}\n` : null
  } catch {
    return null
  }
}

function operationalCycleBridge() {
  return {
    name: 'synapse-operational-cycle-bridge',
    configureServer(server: { middlewares: { use: (route: string, handler: (_req: unknown, response: { statusCode: number; setHeader: (name: string, value: string) => void; end: (body?: string) => void }) => void) => void } }) {
      server.middlewares.use(operationalRoute, (_request, response) => {
        const csv = combinedOperationalCycles()
        response.statusCode = csv ? 200 : 404
        response.setHeader('Content-Type', 'text/csv; charset=utf-8')
        response.setHeader('Cache-Control', 'no-store')
        response.end(csv ?? 'No operational cycle is available.')
      })
    },
    generateBundle(this: { emitFile: (asset: { type: 'asset'; fileName: string; source: string }) => void }) {
      const csv = combinedOperationalCycles()
      if (csv) this.emitFile({ type: 'asset', fileName: operationalRoute.slice(1), source: csv })
    },
  }
}

// Generated model outputs remain the single source of truth. Vite serves them
// directly in development and copies them unchanged for a production build.
export default defineConfig({
  plugins: [react(), operationalCycleBridge()],
  publicDir: '../outputs',
})
