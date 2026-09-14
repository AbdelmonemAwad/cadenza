import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import FolderPicker from '../components/FolderPicker'
import { useI18n } from '../i18n'

type Templates = {
  path_template: string; compilation_template: string; single_template: string
}
type PlanRow = { track_id: number; from: string; to: string; reason: string | null }
type NotPlanned = { track_id: number; path: string; reason: string | null }
type OrganizeResult = {
  dry_run: boolean; moved: number; skipped: number; failed: number; stopped: boolean
  errors: string[]; preview: PlanRow[]; not_planned: NotPlanned[]; unchanged: number
}
type JobRow = {
  id: number; state: string; dry_run: boolean
  result: OrganizeResult | null; error: string | null
}

const SHOWN = 300

/** Renaming and moving files into the template folders had no page: the job
    existed, the templates could be edited in Settings, and nothing could run
    it (#74). This page previews the plan and applies it after a confirmation
    that names the number of moves. */
export default function Organize() {
  const { t, n } = useI18n()
  const [templates, setTemplates] = useState<Templates | null>(null)
  const [path, setPath] = useState('')
  const [browsing, setBrowsing] = useState(false)
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<JobRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    api.get<Templates>('/settings').then(setTemplates).catch(() => {})
  }, [])

  const refresh = useCallback((id: number) => {
    api.get<JobRow>(`/jobs/${id}`).then(setJob).catch((e) => setMessage((e as Error).message))
  }, [])

  // Follow the job this page queued, and show its result the moment it ends.
  // App.tsx dispatches cadenza:job-finished with the job event as detail.
  useEffect(() => {
    if (jobId === null) return
    const onDone = (e: Event) => {
      const detail = (e as CustomEvent<{ job_id?: number }>).detail
      if (detail?.job_id === jobId) refresh(jobId)
    }
    window.addEventListener('cadenza:job-finished', onDone)
    refresh(jobId)
    return () => window.removeEventListener('cadenza:job-finished', onDone)
  }, [jobId, refresh])

  const submit = async (dryRun: boolean) => {
    if (!dryRun) {
      const planned = job?.dry_run && job.state === 'done' && job.result
        ? job.result.moved : null
      const question = planned === null
        ? t('organize.confirmNoPreview')
        : t('organize.confirm', { count: n(planned) })
      if (!confirm(question)) return
    }
    setBusy(true)
    setMessage(null)
    try {
      const body: Record<string, unknown> = { dry_run: dryRun }
      if (path.trim()) body.path = path.trim()
      const r = await api.post<{ job_id: number }>('/metadata/organize', body)
      setJob(null)
      setJobId(r.job_id)
      setMessage(t('jobs.runQueued', { id: r.job_id }))
    } catch (e) {
      setMessage((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const finished = job && (job.state === 'done' || job.state === 'failed' || job.state === 'cancelled')
  const result = job?.result ?? null

  return (
    <>
      <div className="page-head">
        <h1>{t('organize.title')}</h1>
        <p>{t('organize.subtitle')}</p>
      </div>

      <div className="banner">{t('organize.rescanNote')}</div>
      {message && <div className="banner warn">{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('organize.templatesTitle')}</h3>
        <table>
          <tbody>
            <tr>
              <td>{t('organize.templateAlbum')}</td>
              <td className="mono" style={{ direction: 'ltr' }}>{templates?.path_template ?? '…'}</td>
            </tr>
            <tr>
              <td>{t('organize.templateCompilation')}</td>
              <td className="mono" style={{ direction: 'ltr' }}>{templates?.compilation_template ?? '…'}</td>
            </tr>
            <tr>
              <td>{t('organize.templateSingle')}</td>
              <td className="mono" style={{ direction: 'ltr' }}>{templates?.single_template ?? '…'}</td>
            </tr>
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>
          <Link to="/settings">{t('organize.editInSettings')}</Link>
        </p>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('organize.scopeTitle')}</h3>
        <p className="muted" style={{ fontSize: 12.5 }}>{t('organize.scopeHint')}</p>
        <div className="toolbar" style={{ marginBottom: 0 }}>
          <input type="text" value={path} placeholder={t('organize.scopePlaceholder')}
            style={{ maxWidth: 480, direction: 'ltr' }}
            onChange={(e) => setPath(e.target.value)} />
          <button className="btn" disabled={busy} onClick={() => setBrowsing(true)}>
            {t('organize.browse')}
          </button>
          {path && (
            <button className="btn" disabled={busy} onClick={() => setPath('')}>
              {t('organize.clear')}
            </button>
          )}
        </div>
        {browsing && (
          <FolderPicker value={path}
            onPick={(p) => { setPath(p); setBrowsing(false) }}
            onClose={() => setBrowsing(false)} />
        )}

        <div className="toolbar" style={{ marginTop: 14, marginBottom: 0 }}>
          <button className="btn" disabled={busy} onClick={() => submit(true)}>
            {t('organize.preview')}
          </button>
          <button className="btn primary" disabled={busy} onClick={() => submit(false)}>
            {t('organize.apply')}
          </button>
        </div>
      </div>

      {job && !finished && (
        <div className="card">
          <p className="muted" style={{ margin: 0 }}>{t('organize.running', { id: job.id })}</p>
        </div>
      )}

      {job && finished && job.state !== 'done' && (
        <div className="card">
          <div className="banner danger">{job.error ?? job.state}</div>
        </div>
      )}

      {job && finished && job.state === 'done' && result && (
        <div className="card">
          <h3>{result.dry_run ? t('organize.planTitle') : t('organize.resultTitle')}</h3>
          <p style={{ margin: '4px 0 10px' }}>
            <strong>
              {result.dry_run
                ? t('organize.planned', { count: n(result.moved) })
                : t('organize.moved', { count: n(result.moved) })}
            </strong>
            {' · '}{t('organize.unchanged', { count: n(result.unchanged) })}
            {result.not_planned.length > 0 && (
              <>{' · '}{t('organize.notPlanned', { count: n(result.not_planned.length) })}</>
            )}
            {!result.dry_run && result.failed > 0 && (
              <>{' · '}{t('organize.failed', { count: n(result.failed) })}</>
            )}
          </p>
          {result.stopped && <div className="banner warn">{t('organize.stopped')}</div>}
          {result.moved === 0 && result.not_planned.length === 0 && (
            <div className="empty">{t('organize.nothingToDo')}</div>
          )}

          {result.dry_run && result.preview.length > 0 && (
            <>
              <table>
                <thead>
                  <tr>
                    <th>{t('organize.colFrom')}</th>
                    <th>{t('organize.colTo')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.preview.slice(0, SHOWN).map((row) => (
                    <tr key={row.track_id}>
                      <td className="mono truncate" title={row.from} style={{ direction: 'ltr' }}>{row.from}</td>
                      <td className="mono truncate" title={row.to} style={{ direction: 'ltr' }}>{row.to}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result.moved > SHOWN && (
                <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
                  {t('organize.previewTruncated', { count: n(SHOWN) })}
                </p>
              )}
            </>
          )}

          {result.not_planned.length > 0 && (
            <table style={{ marginTop: 12 }}>
              <thead>
                <tr>
                  <th>{t('duplicates.colPath')}</th>
                  <th>{t('organize.colReason')}</th>
                </tr>
              </thead>
              <tbody>
                {result.not_planned.slice(0, SHOWN).map((row) => (
                  <tr key={row.track_id}>
                    <td className="mono truncate" title={row.path} style={{ direction: 'ltr' }}>{row.path}</td>
                    <td className="muted">{row.reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {!result.dry_run && result.errors.length > 0 && (
            <ul className="mono" style={{ fontSize: 12, direction: 'ltr' }}>
              {result.errors.map((err, i) => <li key={i}>{err}</li>)}
            </ul>
          )}
          {!result.dry_run && result.moved > 0 && (
            <div className="banner" style={{ marginTop: 12 }}>{t('organize.rescanNote')}</div>
          )}
        </div>
      )}
    </>
  )
}
