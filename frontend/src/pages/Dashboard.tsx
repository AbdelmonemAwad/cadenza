import { useEffect, useState } from 'react'
import { api, humanBytes } from '../api/client'
import { useI18n } from '../i18n'

type Stats = {
  library: {
    tracks: number; bytes: number; lossless: number; lossy: number
    corrupt: number; missing: number; avg_quality: number
    by_codec: { codec: string; count: number; bytes: number }[]
  }
  quality_gaps: {
    missing_artwork: number; missing_lyrics: number; incomplete_tags: number
    incomplete_albums: {
      albumartist: string | null; album: string
      have: number; expected: number; missing: number
    }[]
  }
  duplicates: { groups: number; files: number; reclaimable_bytes: number }
  quarantine: { items: number; bytes: number }
}

export default function Dashboard() {
  const { t, n } = useI18n()
  const [stats, setStats] = useState<Stats | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.get<Stats>('/dashboard/stats').then(setStats)
    .catch((e) => setError(e.message))

  useEffect(() => {
    load()
    const timer = setInterval(load, 20000)
    const onDone = () => load()
    window.addEventListener('cadenza:job-finished', onDone)
    return () => {
      clearInterval(timer)
      window.removeEventListener('cadenza:job-finished', onDone)
    }
  }, [])

  const run = async (kind: string, params: object = {}) => {
    setBusy(true)
    try { await api.post('/jobs', { kind, params, dry_run: true }) }
    catch (e) { setError((e as Error).message) }
    finally { setBusy(false) }
  }

  if (error) return <div className="banner danger">{t('dashboard.statsError', { error })}</div>
  if (!stats) return <div className="empty">{t('app.loading')}</div>

  const losslessPercent = stats.library.tracks
    ? Math.round((stats.library.lossless / stats.library.tracks) * 100)
    : 0

  return (
    <>
      <div className="page-head">
        <h1>{t('dashboard.title')}</h1>
        <p>{t('dashboard.subtitle')}</p>
        <div className="spacer" />
        <button className="btn" disabled={busy} onClick={() => run('scan')}>
          {t('dashboard.scanLibrary')}
        </button>
        <button className="btn primary" disabled={busy} onClick={() => run('dedup_analyze')}>
          {t('dashboard.analyzeDuplicates')}
        </button>
      </div>

      <div className="grid cols-4">
        <div className="card">
          <div className="stat-label">{t('dashboard.totalTracks')}</div>
          <div className="stat-value">{n(stats.library.tracks)}</div>
          <div className="stat-hint">
            {t('dashboard.consumed', { value: humanBytes(stats.library.bytes) })}
          </div>
        </div>

        <div className="card">
          <div className="stat-label">{t('dashboard.duplicateFiles')}</div>
          <div className={`stat-value ${stats.duplicates.files ? 'warn' : 'ok'}`}>
            {n(stats.duplicates.files)}
          </div>
          <div className="stat-hint">
            {t('dashboard.inGroups', {
              count: stats.duplicates.groups,
              size: humanBytes(stats.duplicates.reclaimable_bytes),
            })}
          </div>
        </div>

        <div className="card">
          <div className="stat-label">{t('dashboard.libraryQuality')}</div>
          <div className="stat-value">{Math.round(stats.library.avg_quality * 100)}%</div>
          <div className="stat-hint">
            {t('dashboard.losslessShare', {
              percent: losslessPercent, count: n(stats.library.lossless),
            })}
          </div>
        </div>

        <div className="card">
          <div className="stat-label">{t('dashboard.inQuarantine')}</div>
          <div className="stat-value">{n(stats.quarantine.items)}</div>
          <div className="stat-hint">
            {t('dashboard.restorable', { size: humanBytes(stats.quarantine.bytes) })}
          </div>
        </div>
      </div>

      {(stats.library.corrupt > 0 || stats.library.missing > 0) && (
        <div className="banner warn" style={{ marginTop: 16 }}>
          {stats.library.corrupt > 0
            && `${t('dashboard.corruptWarning', { count: stats.library.corrupt })} `}
          {stats.library.missing > 0
            && t('dashboard.missingWarning', { count: stats.library.missing })}
        </div>
      )}

      <div className="grid cols-2" style={{ marginTop: 16 }}>
        <div className="card">
          <h3>{t('dashboard.metadataGaps')}</h3>
          <table>
            <tbody>
              <tr>
                <td>{t('dashboard.noArtwork')}</td>
                <td style={{ textAlign: 'end' }}>{n(stats.quality_gaps.missing_artwork)}</td>
              </tr>
              <tr>
                <td>{t('dashboard.noLyrics')}</td>
                <td style={{ textAlign: 'end' }}>{n(stats.quality_gaps.missing_lyrics)}</td>
              </tr>
              <tr>
                <td>{t('dashboard.incompleteTags')}</td>
                <td style={{ textAlign: 'end' }}>{n(stats.quality_gaps.incomplete_tags)}</td>
              </tr>
            </tbody>
          </table>
          <button className="btn primary sm" style={{ marginTop: 12 }} disabled={busy}
            onClick={() => run('enrich', { only_incomplete: true, limit: 300 })}>
            {t('dashboard.previewEnrich')}
          </button>
        </div>

        <div className="card">
          <h3>{t('dashboard.formatBreakdown')}</h3>
          <table>
            <thead>
              <tr>
                <th>{t('dashboard.format')}</th>
                <th>{t('dashboard.files')}</th>
                <th>{t('dashboard.size')}</th>
              </tr>
            </thead>
            <tbody>
              {stats.library.by_codec.slice(0, 8).map((c) => (
                <tr key={c.codec}>
                  <td><span className="tag">{c.codec}</span></td>
                  <td>{n(c.count)}</td>
                  <td className="muted">{humanBytes(c.bytes)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {stats.quality_gaps.incomplete_albums.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>{t('dashboard.incompleteAlbums')}</h3>
          <table>
            <thead>
              <tr>
                <th>{t('dashboard.artist')}</th>
                <th>{t('dashboard.album')}</th>
                <th>{t('dashboard.have')}</th>
                <th>{t('dashboard.expected')}</th>
                <th>{t('dashboard.missing')}</th>
              </tr>
            </thead>
            <tbody>
              {stats.quality_gaps.incomplete_albums.slice(0, 15).map((a, i) => (
                <tr key={i}>
                  <td className="truncate">{a.albumartist ?? '—'}</td>
                  <td className="truncate">{a.album}</td>
                  <td>{a.have}</td>
                  <td>{a.expected}</td>
                  <td><span className="tag warn">{a.missing}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
