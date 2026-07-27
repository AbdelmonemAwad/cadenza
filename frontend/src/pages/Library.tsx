import { useEffect, useState } from 'react'
import { api, humanBytes, humanDuration } from '../api/client'
import { useI18n } from '../i18n'

type Track = {
  id: number; path: string; filename: string; codec: string | null
  lossless: boolean; bitrate: number | null; duration: number | null
  size_bytes: number; title: string | null; artist: string | null
  album: string | null; year: number | null; has_artwork: boolean
  has_lyrics: boolean; tag_completeness: number; quality_score: number
  status: string
}

type Lookup = {
  confidence: number
  proposed: Record<string, unknown>
  field_sources: Record<string, string>
  conflicts: Record<string, string[]>
}

const PAGE = 50

export default function Library() {
  const { t, n } = useI18n()
  const [items, setItems] = useState<Track[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Track | null>(null)
  const [lookup, setLookup] = useState<Lookup | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  const load = () => {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) })
    if (query) params.set('q', query)
    if (filter === 'no_art') params.set('missing_artwork', 'true')
    if (filter === 'incomplete') params.set('incomplete_tags', 'true')
    if (filter === 'lossless') params.set('lossless', 'true')
    if (filter === 'corrupt') params.set('status', 'corrupt')
    api.get<{ total: number; items: Track[] }>(`/library/tracks?${params}`)
      .then((r) => { setItems(r.items); setTotal(r.total) })
      .catch((e) => setMessage(e.message))
  }

  useEffect(() => { load() }, [offset, filter])

  const inspect = async (track: Track) => {
    setSelected(track); setLookup(null); setBusy(true); setMessage(null)
    try { setLookup(await api.get<Lookup>(`/metadata/lookup/${track.id}`)) }
    catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const applyOne = async (track: Track, dryRun: boolean) => {
    setBusy(true)
    try {
      const r = await api.post<{ applied: boolean; error: string | null }>(
        `/metadata/apply/${track.id}?dry_run=${dryRun}`)
      setMessage(r.error ?? (dryRun ? t('library.previewOnly') : t('library.applied')))
      if (!dryRun) load()
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('library.title')}</h1>
        <p>{t('library.trackCount', { count: n(total) })}</p>
        <div className="spacer" />
        <button className="btn" onClick={() => api.post('/jobs', { kind: 'scan', params: {} })}>
          {t('library.scan')}
        </button>
        <button className="btn primary"
          onClick={() => api.post('/metadata/enrich', { only_incomplete: true, dry_run: true })}>
          {t('library.enrichIncomplete')}
        </button>
      </div>

      {message && <div className="banner">{message}</div>}

      <div className="toolbar">
        <input type="text" placeholder={t('library.searchPlaceholder')} value={query}
          style={{ maxWidth: 340 }}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') { setOffset(0); load() } }} />
        <button className="btn" onClick={() => { setOffset(0); load() }}>{t('app.search')}</button>
        <select value={filter} onChange={(e) => { setOffset(0); setFilter(e.target.value) }}
          style={{ width: 210 }}>
          <option value="">{t('library.filterAll')}</option>
          <option value="incomplete">{t('library.filterIncomplete')}</option>
          <option value="no_art">{t('library.filterNoArtwork')}</option>
          <option value="lossless">{t('library.filterLossless')}</option>
          <option value="corrupt">{t('library.filterCorrupt')}</option>
        </select>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>{t('library.colTitle')}</th>
              <th>{t('library.colArtist')}</th>
              <th>{t('library.colAlbum')}</th>
              <th>{t('library.colYear')}</th>
              <th>{t('library.colFormat')}</th>
              <th>{t('library.colDuration')}</th>
              <th>{t('library.colSize')}</th>
              <th>{t('library.colCompleteness')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items.map((track) => (
              <tr key={track.id}>
                <td className="truncate" style={{ maxWidth: 260 }} title={track.path}>
                  {track.title ?? track.filename}
                </td>
                <td className="truncate" style={{ maxWidth: 160 }}>{track.artist ?? '—'}</td>
                <td className="truncate" style={{ maxWidth: 180 }}>{track.album ?? '—'}</td>
                <td>{track.year ?? '—'}</td>
                <td>
                  <span className={`tag ${track.lossless ? 'ok' : ''}`}>{track.codec ?? '?'}</span>
                  {!track.has_artwork && (
                    <span className="tag warn" style={{ marginInlineStart: 4 }}>
                      {t('library.noArtworkBadge')}
                    </span>
                  )}
                </td>
                <td>{humanDuration(track.duration)}</td>
                <td className="muted">{humanBytes(track.size_bytes)}</td>
                <td>
                  <div className="bar" style={{ width: 60 }}>
                    <i style={{ width: `${Math.round(track.tag_completeness * 100)}%` }} />
                  </div>
                </td>
                <td>
                  <button className="btn sm" disabled={busy} onClick={() => inspect(track)}>
                    {t('library.inspect')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className="btn sm" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('app.previous')}</button>
          <span className="muted">
            {offset + 1}–{Math.min(offset + PAGE, total)} {t('app.of')} {n(total)}
          </span>
          <button className="btn sm" disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}>{t('app.next')}</button>
        </div>
      </div>

      {selected && (
        <div className="card" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <h3 style={{ margin: 0 }}>{t('library.lookupTitle')}</h3>
            <div className="spacer" />
            <button className="btn sm" onClick={() => { setSelected(null); setLookup(null) }}>
              {t('app.close')}
            </button>
          </div>
          <p className="mono truncate" title={selected.path}>{selected.path}</p>

          {busy && <div className="empty">{t('library.lookupRunning')}</div>}

          {lookup && (
            <>
              <div className="banner">
                {t('library.overallConfidence', {
                  percent: Math.round(lookup.confidence * 100),
                })}
                {lookup.confidence < 0.55 && t('library.belowThreshold')}
              </div>
              <table>
                <thead>
                  <tr>
                    <th>{t('library.colField')}</th>
                    <th>{t('library.colProposed')}</th>
                    <th>{t('library.colSource')}</th>
                    <th>{t('library.colConflict')}</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(lookup.proposed)
                    .filter(([, v]) => v !== null && v !== '' && v !== false)
                    .map(([key, value]) => (
                      <tr key={key}>
                        <td className="mono">{key}</td>
                        <td>{String(value)}</td>
                        <td><span className="tag blue">{lookup.field_sources[key] ?? '—'}</span></td>
                        <td className="muted mono truncate" style={{ maxWidth: 320 }}>
                          {(lookup.conflicts[key] ?? []).join(' · ')}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
              <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
                <button className="btn" disabled={busy} onClick={() => applyOne(selected, true)}>
                  {t('library.previewApply')}
                </button>
                <button className="btn primary" disabled={busy}
                  onClick={() => applyOne(selected, false)}>
                  {t('library.applyToFile')}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </>
  )
}
