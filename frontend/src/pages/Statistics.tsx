import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n } from '../i18n'

type Stats = {
  window_days: number
  since: string
  library: {
    tracks: number; bytes: number; seconds: number
    artists: number; albums: number
    lossless: number; with_artwork: number; with_lyrics: number
  }
  jobs: { kind: string; state: string; count: number }[]
  actions: { action: string; count: number }[]
  per_day: { day: string; count: number }[]
  space: { library_bytes: number; in_quarantine_bytes: number; reclaimable_bytes: number }
  totals: { jobs: number; failed_jobs: number; actions: number }
}

type LogView = {
  path: string; exists: boolean; size_bytes?: number
  levels?: string[]; lines: string[]; note?: string
}

const WINDOWS = [7, 30, 90, 365]
const STATE_TAG: Record<string, string> = {
  done: 'ok', failed: 'danger', running: 'blue', cancelled: 'warn', pending: '',
}

function bytes(value: number): string {
  if (!value) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)))
  return `${(value / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`
}

function duration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  return days ? `${days}d ${hours}h` : `${hours}h`
}

/**
 * Activity per day, drawn by hand.
 *
 * No chart library: the frontend has three dependencies and none of them is
 * one, and pulling in a charting package to draw thirty bars would be larger
 * than the rest of the bundle. Bars are plain divs, so they inherit the theme
 * and the right-to-left layout for free.
 */
function ActivityChart({ data }: { data: { day: string; count: number }[] }) {
  const { t, n } = useI18n()
  const peak = Math.max(1, ...data.map((d) => d.count))
  if (!data.length) return <p className="muted">{t('app.empty')}</p>

  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 120,
      overflowX: 'auto', paddingTop: 8 }}>
      {data.map((d) => (
        <div key={d.day}
          title={`${d.day} — ${n(d.count)}`}
          style={{
            flex: '1 0 6px', minWidth: 6,
            height: `${Math.max(2, (d.count / peak) * 100)}%`,
            background: d.count ? 'var(--accent, #0f7bd8)' : 'var(--border, #ddd)',
            borderRadius: 2,
          }} />
      ))}
    </div>
  )
}

export default function Statistics() {
  const { t, n, d } = useI18n()
  const [days, setDays] = useState(30)
  const [stats, setStats] = useState<Stats | null>(null)
  const [logView, setLogView] = useState<LogView | null>(null)
  const [level, setLevel] = useState('')
  const [contains, setContains] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    api.get<Stats>(`/statistics?days=${days}`)
      .then(setStats)
      .catch((e) => setMessage((e as Error).message))
  }, [days])

  const loadLog = () => {
    const params = new URLSearchParams({ lines: '300' })
    if (level) params.set('level', level)
    if (contains) params.set('contains', contains)
    api.get<LogView>(`/statistics/log?${params}`)
      .then(setLogView)
      // Surfaced, not swallowed. An empty panel with no explanation is how the
      // rest of this app used to report a failed request.
      .catch((e) => setMessage((e as Error).message))
  }
  useEffect(() => { loadLog() }, [level])

  if (!stats) {
    return <div className="empty">{message ?? t('app.loading')}</div>
  }

  const lib = stats.library
  return (
    <>
      <div className="page-head">
        <h1>{t('stats.title')}</h1>
        <p>{t('stats.subtitle')}</p>
        <div className="spacer" />
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}
          style={{ width: 180 }}>
          {WINDOWS.map((w) => (
            <option key={w} value={w}>{t('stats.lastDays', { days: String(w) })}</option>
          ))}
        </select>
      </div>
      {message && <div className="banner">{message}</div>}

      <div className="grid cols-4" style={{ marginBottom: 16 }}>
        <div className="card">
          <div className="stat-label">{t('stats.tracks')}</div>
          <h2 style={{ margin: '4px 0' }}>{n(lib.tracks)}</h2>
          <p className="muted">{n(lib.artists)} · {n(lib.albums)}</p>
        </div>
        <div className="card">
          <div className="stat-label">{t('stats.size')}</div>
          <h2 style={{ margin: '4px 0' }}>{bytes(lib.bytes)}</h2>
          <p className="muted">{duration(lib.seconds)}</p>
        </div>
        <div className="card">
          <div className="stat-label">{t('stats.jobsRun')}</div>
          <h2 style={{ margin: '4px 0' }}>{n(stats.totals.jobs)}</h2>
          <p className="muted">
            <span className={`tag ${stats.totals.failed_jobs ? 'danger' : 'ok'}`}>
              {n(stats.totals.failed_jobs)} {t('stats.failed')}
            </span>
          </p>
        </div>
        <div className="card">
          <div className="stat-label">{t('stats.reclaimable')}</div>
          <h2 style={{ margin: '4px 0' }}>{bytes(stats.space.reclaimable_bytes)}</h2>
          <p className="muted">
            {bytes(stats.space.in_quarantine_bytes)} {t('stats.inQuarantine')}
          </p>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('stats.activity')}</h3>
        <p className="muted">{t('stats.activityHint')}</p>
        <ActivityChart data={stats.per_day} />
        <p className="muted" style={{ marginTop: 6 }}>
          {d(stats.since)} → {t('stats.today')}
        </p>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 16 }}>
        <div className="card">
          <h3>{t('stats.coverage')}</h3>
          {([
            ['stats.lossless', lib.lossless],
            ['stats.withArtwork', lib.with_artwork],
            ['stats.withLyrics', lib.with_lyrics],
          ] as const).map(([key, value]) => {
            const pct = lib.tracks ? Math.round((value / lib.tracks) * 100) : 0
            return (
              <div key={key} style={{ margin: '8px 0' }}>
                <div className="stat-label">
                  {t(key)} — {n(value)} ({pct}%)
                </div>
                <div style={{ height: 6, background: 'var(--border, #eee)',
                  borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${pct}%`, height: '100%',
                    background: 'var(--accent, #0f7bd8)' }} />
                </div>
              </div>
            )
          })}
        </div>

        <div className="card">
          <h3>{t('stats.byJob')}</h3>
          {stats.jobs.length ? (
            <table>
              <tbody>
                {stats.jobs.map((j) => (
                  <tr key={`${j.kind}-${j.state}`}>
                    <td>{j.kind}</td>
                    <td><span className={`tag ${STATE_TAG[j.state] ?? ''}`}>{j.state}</span></td>
                    <td className="mono">{n(j.count)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted">{t('app.empty')}</p>}
        </div>
      </div>

      <div className="card">
        <div className="toolbar">
          <h3 style={{ margin: 0 }}>{t('stats.logTitle')}</h3>
          <div className="spacer" />
          <select value={level} onChange={(e) => setLevel(e.target.value)}
            style={{ width: 150 }}>
            <option value="">{t('logs.allLevels')}</option>
            {(logView?.levels ?? []).map((l) => (
              <option key={l} value={l}>{l}</option>
            ))}
          </select>
          <input type="text" placeholder={t('stats.logFilter')} value={contains}
            onChange={(e) => setContains(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && loadLog()} style={{ width: 200 }} />
          <button className="btn sm" onClick={loadLog}>{t('stats.refresh')}</button>
        </div>

        <p className="muted">{t('stats.logHint')}</p>

        {logView && !logView.exists && <p className="muted">{logView.note}</p>}
        {logView?.exists && (
          <>
            <p className="muted mono truncate" style={{ direction: 'ltr' }}>
              {logView.path} · {bytes(logView.size_bytes ?? 0)}
            </p>
            <pre className="mono" style={{
              direction: 'ltr', textAlign: 'left', maxHeight: 420,
              overflow: 'auto', margin: 0, fontSize: 12, lineHeight: 1.5,
            }}>
              {logView.lines.length ? logView.lines.join('\n') : t('app.empty')}
            </pre>
          </>
        )}
      </div>
    </>
  )
}
