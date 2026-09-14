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
  const [incompleteCount, setIncompleteCount] = useState<number | null>(null)
  const [enrich, setEnrich] = useState({
    incompleteOnly: true, limit: 500, overwrite: false,
    artwork: true, lyrics: true, minConfidence: 0.55,
  })

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
    api.get<{ total: number }>('/library/tracks?incomplete_tags=true&limit=1')
      .then((r) => setIncompleteCount(r.total))
      .catch(() => setIncompleteCount(null))
  }

  // The one library-wide button posted dry_run: true and nothing else, so
  // every enrichment ever run on a real library was a preview, and the only
  // real write the page offered was the inspector's one track at a time (#74).
  const runEnrich = async (dryRun: boolean) => {
    const scoped = enrich.incompleteOnly && incompleteCount !== null
      ? Math.min(incompleteCount, enrich.limit) : enrich.limit
    if (!dryRun && !confirm(t('library.enrichConfirm', { count: n(scoped) }))) return
    setBusy(true)
    setMessage(null)
    try {
      const r = await api.post<{ job_id: number }>('/metadata/enrich', {
        only_incomplete: enrich.incompleteOnly, limit: enrich.limit,
        overwrite: enrich.overwrite, artwork: enrich.artwork, lyrics: enrich.lyrics,
        min_confidence: enrich.minConfidence, dry_run: dryRun,
      })
      setMessage(t('jobs.runQueued', { id: r.job_id }))
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  // Reload when a job finishes, not only when the filter changes. This page
  // owns the Scan button, and without the listener the sequence was: click
  // Scan, watch the job strip run to completion, watch it disappear — and the
  // table is still empty and the header still says "0 tracks". Nothing
  // reloaded until the user pressed Search or navigated away and back, so on a
  // fresh install the scan looked like it had done nothing at all.
  //
  // App.tsx dispatches cadenza:job-finished for exactly this, and Dashboard,
  // Duplicates and Convert all listen. The page with the button did not.
  useEffect(() => {
    load()
    const onJobFinished = () => load()
    window.addEventListener('cadenza:job-finished', onJobFinished)
    return () => window.removeEventListener('cadenza:job-finished', onJobFinished)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [offset, filter])

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
      </div>

      {message && <div className="banner">{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('library.enrichTitle')}</h3>
        <p className="muted" style={{ fontSize: 12.5 }}>{t('library.enrichIntro')}</p>
        <div className="grid cols-3">
          <div>
            <label className="check">
              <input type="checkbox" checked={enrich.incompleteOnly}
                onChange={(e) => setEnrich({ ...enrich, incompleteOnly: e.target.checked })} />
              {t('library.enrichIncompleteOnly')}
            </label>
            {incompleteCount !== null && (
              <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                {t('library.enrichIncompleteCount', { count: n(incompleteCount) })}
              </p>
            )}
            <label className="check" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={enrich.overwrite}
                onChange={(e) => setEnrich({ ...enrich, overwrite: e.target.checked })} />
              {t('library.enrichOverwrite')}
            </label>
          </div>
          <div>
            <label className="check">
              <input type="checkbox" checked={enrich.artwork}
                onChange={(e) => setEnrich({ ...enrich, artwork: e.target.checked })} />
              {t('library.enrichArtwork')}
            </label>
            <label className="check" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={enrich.lyrics}
                onChange={(e) => setEnrich({ ...enrich, lyrics: e.target.checked })} />
              {t('library.enrichLyrics')}
            </label>
          </div>
          <div className="grid cols-2">
            <label className="field">
              <span>{t('library.enrichLimit')}</span>
              <input type="number" min={1} max={5000} value={enrich.limit}
                onChange={(e) => setEnrich({ ...enrich, limit: Math.max(1, Number(e.target.value) || 1) })} />
            </label>
            <label className="field">
              <span>{t('library.enrichMinConfidence')}</span>
              <input type="number" min={0.3} max={1} step={0.05} value={enrich.minConfidence}
                onChange={(e) => setEnrich({ ...enrich, minConfidence: Number(e.target.value) || 0.55 })} />
            </label>
          </div>
        </div>
        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className="btn" disabled={busy} onClick={() => runEnrich(true)}>
            {t('library.enrichPreview')}
          </button>
          <button className="btn primary" disabled={busy} onClick={() => runEnrich(false)}>
            {t('library.enrichApply')}
          </button>
        </div>
      </div>

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
