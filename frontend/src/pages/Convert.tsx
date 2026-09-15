import { useCallback, useEffect, useRef, useState } from 'react'
import { api, humanBytes } from '../api/client'
import { useI18n, type TranslationKey } from '../i18n'

type Preset = {
  name: string; ext: string; lossless: boolean; label: string; description: string
}
type PresetResponse = {
  ffmpeg_available: boolean; lossless: Preset[]; lossy: Preset[]
}
type Candidate = {
  track_id: number; path: string; codec: string | null; bitrate: number | null
  size_bytes: number; suggested_preset: string
  reason: 'legacy_codec' | 'oversized_lossless'
}
type CodecRow = {
  codec: string; count: number; bytes: number; lossless: boolean; legacy: boolean
}

type ConvertItem = {
  src: string; dst: string | null; ok: boolean; error: string | null
  skipped_reason: string | null; src_bytes: number; dst_bytes: number
}
type ConvertResult = {
  dry_run: boolean; preset: string; total: number; converted: number; failed: number
  skipped: number; stopped: boolean; saved_bytes: number; written_bytes: number
  originals_kept: boolean; sources_quarantined: number; items: ConvertItem[]
}
type JobRow = {
  id: number; state: string; dry_run: boolean; processed: number; total: number
  result: ConvertResult | null; error: string | null
}

type Scope =
  | { kind: 'legacy' }
  | { kind: 'codec'; codec: string }
  | { kind: 'suggested'; ids: number[] }

/** Preset names the UI has localised copy for. A preset added to the backend
    but not listed here falls back to the English label the API supplies. */
const LOCALISED_PRESETS = [
  'flac', 'flac_16_44', 'alac', 'wav', 'aac_256', 'aac_vbr_high',
  'mp3_v0', 'mp3_320', 'mp3_192', 'opus_160', 'opus_96', 'ogg_q6',
] as const

function presetKey(name: string, part: 'label' | 'desc'): TranslationKey | null {
  return (LOCALISED_PRESETS as readonly string[]).includes(name)
    ? (`presets.${name}.${part}` as TranslationKey)
    : null
}

export default function Convert() {
  const { t, n } = useI18n()
  const [presets, setPresets] = useState<PresetResponse | null>(null)
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [codecs, setCodecs] = useState<CodecRow[]>([])
  const [preset, setPreset] = useState('flac')
  const [keepOriginal, setKeepOriginal] = useState(true)
  const [limit, setLimit] = useState(200)
  const [scope, setScope] = useState<Scope>({ kind: 'legacy' })
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [candidatesLoaded, setCandidatesLoaded] = useState(false)
  const autoScoped = useRef(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<JobRow | null>(null)

  // The page used to say "follow it on the Jobs page" and never mention the
  // job again, and the Jobs page read 0/N for the whole run: a four-hour
  // conversion of 1,610 files was taken for one that had done nothing (#81).
  // The job this page queued is followed here, and its result shown.
  const refresh = useCallback((id: number) => {
    api.get<JobRow>(`/jobs/${id}`).then(setJob).catch(() => {})
  }, [])

  useEffect(() => {
    if (jobId === null) return
    const onEvent = (e: Event) => {
      const detail = (e as CustomEvent<{ job_id?: number }>).detail
      if (detail?.job_id === jobId) refresh(jobId)
    }
    window.addEventListener('cadenza:job-finished', onEvent)
    refresh(jobId)
    const timer = setInterval(() => refresh(jobId), 5000)
    return () => {
      window.removeEventListener('cadenza:job-finished', onEvent)
      clearInterval(timer)
    }
  }, [jobId, refresh])

  const load = () => {
    api.get<PresetResponse>('/convert/presets').then(setPresets).catch(() => {})
    api.get<{ items: Candidate[] }>('/convert/candidates?limit=200')
      .then((r) => { setCandidates(r.items); setCandidatesLoaded(true) }).catch(() => {})
    api.get<CodecRow[]>('/convert/codecs').then(setCodecs).catch(() => {})
  }

  useEffect(() => {
    load()
    const onDone = () => load()
    window.addEventListener('cadenza:job-finished', onDone)
    return () => window.removeEventListener('cadenza:job-finished', onDone)
  }, [])

  // The legacy scope is the default, and on a library with no legacy formats
  // it is empty: the start button sat disabled with no word on why, and the
  // page read as "conversion does not work" (#75). Open on the engine's
  // suggestions instead when there are any. Once, not on every reload.
  useEffect(() => {
    if (autoScoped.current || codecs.length === 0 || !candidatesLoaded) return
    autoScoped.current = true
    const legacy = codecs.filter((c) => c.legacy).reduce((a, c) => a + c.count, 0)
    if (legacy === 0 && candidates.length > 0) {
      setScope({ kind: 'suggested', ids: candidates.map((c) => c.track_id) })
    }
  }, [codecs, candidates, candidatesLoaded])

  const scopeLabel = () => {
    if (scope.kind === 'codec') return t('convert.selectionCodec', { codec: scope.codec })
    if (scope.kind === 'suggested') return t('convert.selectionSuggested')
    return scopeCount() === 0 ? t('convert.selectionNone') : t('convert.selectionAll')
  }

  const scopeCount = () => {
    if (scope.kind === 'codec') {
      return codecs.find((c) => c.codec === scope.codec)?.count ?? 0
    }
    if (scope.kind === 'suggested') return scope.ids.length
    return codecs.filter((c) => c.legacy).reduce((a, c) => a + c.count, 0)
  }

  const submit = async (dryRun: boolean) => {
    const count = Math.min(scopeCount(), limit)
    if (!dryRun) {
      if (!keepOriginal && !confirm(t('convert.confirmDelete'))) return
      if (!confirm(t('convert.confirmConvert', { count, preset }))) return
    }

    const body: Record<string, unknown> = {
      preset, keep_original: keepOriginal, limit, dry_run: dryRun,
      legacy_only: scope.kind === 'legacy',
    }
    if (scope.kind === 'codec') body.source_codecs = [scope.codec]
    if (scope.kind === 'suggested') body.track_ids = scope.ids.slice(0, limit)

    setBusy(true)
    setMessage(null)
    try {
      const r = await api.post<{ job_id: number }>('/convert', body)
      setJob(null)
      setJobId(r.job_id)
      setMessage(dryRun ? t('convert.previewStarted') : t('convert.started'))
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const ffmpegMissing = presets && !presets.ffmpeg_available
  const finished = job !== null && job.state !== 'running' && job.state !== 'pending'
  const result = job?.result ?? null

  const renderPreset = (p: Preset, lossless: boolean) => {
    const labelKey = presetKey(p.name, 'label')
    const descKey = presetKey(p.name, 'desc')
    return (
      <button type="button" key={p.name}
        className={`preset-card${preset === p.name ? ' selected' : ''}`}
        onClick={() => setPreset(p.name)}>
        <div className="name">
          {labelKey ? t(labelKey) : p.label}{' '}
          <span className={`tag ${lossless ? 'ok' : ''}`}>{p.ext}</span>
        </div>
        <div className="desc">{descKey ? t(descKey) : p.description}</div>
      </button>
    )
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('convert.title')}</h1>
        <p>{t('convert.subtitle')}</p>
      </div>

      {ffmpegMissing && <div className="banner danger">{t('convert.ffmpegMissing')}</div>}
      <div className="banner">{t('convert.safetyNotice')}</div>
      {message && <div className="banner warn">{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('convert.target')}</h3>

        <div className="group-label">{t('convert.losslessGroup')}</div>
        <div className="preset-grid">
          {presets?.lossless.map((p) => renderPreset(p, true))}
        </div>

        <div className="group-label">{t('convert.lossyGroup')}</div>
        <div className="preset-grid">
          {presets?.lossy.map((p) => renderPreset(p, false))}
        </div>

        <hr style={{ border: 0, borderTop: '1px solid var(--border)', margin: '18px 0 14px' }} />

        <div className="grid cols-3">
          <div>
            <div className="stat-label">{t('convert.selection')}</div>
            <p style={{ margin: '4px 0' }}><strong>{scopeLabel()}</strong></p>
            <p className="muted" style={{ margin: 0 }}>
              {t('convert.fileCount', { count: n(scopeCount()) })}
            </p>
          </div>
          <label className="field">
            <span>{t('convert.limit')}</span>
            <input type="number" min={1} max={5000} value={limit}
              onChange={(e) => setLimit(Number(e.target.value))} />
          </label>
          <div>
            <label className="check">
              <input type="checkbox" checked={keepOriginal}
                onChange={(e) => setKeepOriginal(e.target.checked)} />
              {t('convert.keepOriginal')}
            </label>
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>
              {t('convert.keepOriginalHint')}
            </p>
          </div>
        </div>

        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className="btn" disabled={busy || !!ffmpegMissing || !scopeCount()}
            onClick={() => submit(true)}>
            {t('convert.startPreview')}
          </button>
          <button className="btn primary" disabled={busy || !!ffmpegMissing || !scopeCount()}
            onClick={() => submit(false)}>
            {t('convert.startConvert')}
          </button>
        </div>
        {!ffmpegMissing && scopeCount() === 0 && (
          <p className="muted" style={{ fontSize: 12.5, margin: '8px 0 0' }}>
            {t('convert.emptySelectionHint')}
          </p>
        )}
      </div>

      {job && !finished && (
        <div className="card" style={{ marginBottom: 16 }}>
          <p className="muted" style={{ margin: 0 }}>
            {t('convert.running', { id: job.id, done: n(job.processed), total: n(job.total) })}
          </p>
        </div>
      )}

      {job && finished && job.state !== 'done' && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="banner danger">{job.error ?? job.state}</div>
        </div>
      )}

      {job && finished && job.state === 'done' && result && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3>{result.dry_run ? t('convert.previewTitle') : t('convert.resultTitle')}</h3>
          <p style={{ margin: '4px 0 10px' }}>
            <strong>
              {result.dry_run
                ? t('convert.wouldConvert', { count: n(result.converted) })
                : t('convert.resultConverted', { count: n(result.converted) })}
            </strong>
            {result.failed > 0 && <>{' · '}{t('convert.resultFailed', { count: n(result.failed) })}</>}
            {result.skipped > 0 && <>{' · '}{t('convert.resultSkipped', { count: n(result.skipped) })}</>}
          </p>
          {result.stopped && <div className="banner warn">{t('organize.stopped')}</div>}
          {!result.dry_run && result.converted > 0 && (
            <>
              <p style={{ margin: '0 0 10px' }}>
                {result.originals_kept
                  ? t('convert.keptNote', { count: n(result.converted), size: humanBytes(result.written_bytes) })
                  : t('convert.replacedNote', { size: humanBytes(result.saved_bytes), count: n(result.sources_quarantined) })}
              </p>
              <div className="banner">{t('organize.rescanNote')}</div>
            </>
          )}
          {result.items.some((i) => !i.ok && !i.skipped_reason) && (
            <table style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th>{t('duplicates.colPath')}</th>
                  <th>{t('convert.colError')}</th>
                </tr>
              </thead>
              <tbody>
                {result.items.filter((i) => !i.ok && !i.skipped_reason).slice(0, 20).map((i) => (
                  <tr key={i.src}>
                    <td className="mono truncate" title={i.src} style={{ direction: 'ltr' }}>{i.src}</td>
                    <td className="muted">{i.error ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('convert.byFormat')}</h3>
        <table>
          <thead>
            <tr>
              <th>{t('convert.colCodec')}</th>
              <th>{t('convert.colCount')}</th>
              <th>{t('convert.colTotalSize')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {codecs.map((c) => (
              <tr key={c.codec}>
                <td>
                  <span className={`tag ${c.lossless ? 'ok' : ''}`}>{c.codec}</span>
                  {c.legacy && (
                    <span className="tag danger" style={{ marginInlineStart: 4 }}>
                      {t('convert.legacyBadge')}
                    </span>
                  )}
                </td>
                <td>{n(c.count)}</td>
                <td className="muted">{humanBytes(c.bytes)}</td>
                <td>
                  <button className="btn sm"
                    onClick={() => setScope({ kind: 'codec', codec: c.codec })}>
                    {t('convert.convertThis')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <h3 style={{ margin: 0 }}>{t('convert.suggestions')}</h3>
          <div className="spacer" />
          {candidates.length > 0 && (
            <button className="btn sm"
              onClick={() => setScope({
                kind: 'suggested', ids: candidates.map((c) => c.track_id),
              })}>
              {t('convert.convertThis')}
            </button>
          )}
        </div>
        <p className="muted" style={{ fontSize: 12.5 }}>{t('convert.suggestionsHint')}</p>

        {candidates.length === 0 && <div className="empty">{t('convert.noCandidates')}</div>}

        {candidates.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>{t('duplicates.colPath')}</th>
                <th>{t('convert.colCodec')}</th>
                <th>{t('duplicates.colBitrate')}</th>
                <th>{t('dashboard.size')}</th>
                <th>{t('convert.target')}</th>
                <th>{t('duplicates.colReason')}</th>
              </tr>
            </thead>
            <tbody>
              {candidates.slice(0, 100).map((c) => (
                <tr key={c.track_id}>
                  <td className="mono truncate" title={c.path}>{c.path}</td>
                  <td><span className="tag">{c.codec ?? '—'}</span></td>
                  <td>{c.bitrate ? `${Math.round(c.bitrate / 1000)}k` : '—'}</td>
                  <td className="muted">{humanBytes(c.size_bytes)}</td>
                  <td><span className="tag blue">{c.suggested_preset}</span></td>
                  <td className="muted">
                    {c.reason === 'legacy_codec'
                      ? t('convert.reasonLegacy')
                      : t('convert.reasonOversized')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
