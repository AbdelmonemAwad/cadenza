import { Fragment, useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n, type TranslationKey } from '../i18n'

type Row = {
  id: number; ts: string; action: string; level: string
  track_id: number | null; job_id: number | null
  src: string | null; dst: string | null
  detail: Record<string, unknown> | null; reversible: boolean
}

const LEVEL_TAG: Record<string, string> = { info: '', warning: 'warn', error: 'danger' }

const ACTIONS: { value: string; key: TranslationKey }[] = [
  { value: 'quarantine', key: 'logs.actionQuarantine' },
  { value: 'restore', key: 'logs.actionRestore' },
  { value: 'organize', key: 'logs.actionOrganize' },
  { value: 'enrich', key: 'logs.actionEnrich' },
  { value: 'manual_tag_edit', key: 'logs.actionManualEdit' },
  { value: 'purge', key: 'logs.actionPurge' },
]

const PAGE = 100

export default function Logs() {
  const { t, n, d } = useI18n()
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [action, setAction] = useState('')
  const [level, setLevel] = useState('')
  const [expanded, setExpanded] = useState<number | null>(null)

  useEffect(() => {
    const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset) })
    if (action) params.set('action', action)
    if (level) params.set('level', level)
    api.get<{ total: number; items: Row[] }>(`/dashboard/audit?${params}`)
      .then((r) => { setRows(r.items); setTotal(r.total) })
      .catch(() => {})
  }, [offset, action, level])

  return (
    <>
      <div className="page-head">
        <h1>{t('logs.title')}</h1>
        <p>{t('logs.subtitle')}</p>
      </div>

      <div className="toolbar">
        <select value={action} onChange={(e) => { setOffset(0); setAction(e.target.value) }}
          style={{ width: 220 }}>
          <option value="">{t('logs.allActions')}</option>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{t(a.key)}</option>)}
        </select>
        <select value={level} onChange={(e) => { setOffset(0); setLevel(e.target.value) }}
          style={{ width: 170 }}>
          <option value="">{t('logs.allLevels')}</option>
          <option value="info">{t('logs.levelInfo')}</option>
          <option value="warning">{t('logs.levelWarning')}</option>
          <option value="error">{t('logs.levelError')}</option>
        </select>
        <span className="muted">{t('logs.entryCount', { count: n(total) })}</span>
      </div>

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>{t('logs.colTime')}</th>
              <th>{t('logs.colAction')}</th>
              <th>{t('logs.colSource')}</th>
              <th>{t('logs.colDestination')}</th>
              <th>{t('logs.colJob')}</th>
              <th>{t('logs.colReversible')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Fragment key={row.id}>
                <tr>
                  <td className="muted mono">{d(row.ts)}</td>
                  <td><span className={`tag ${LEVEL_TAG[row.level] ?? ''}`}>{row.action}</span></td>
                  <td className="mono truncate" style={{ maxWidth: 320 }} title={row.src ?? ''}>
                    {row.src ?? '—'}
                  </td>
                  <td className="mono truncate" style={{ maxWidth: 320 }} title={row.dst ?? ''}>
                    {row.dst ?? '—'}
                  </td>
                  <td className="mono">{row.job_id ?? '—'}</td>
                  <td>
                    <span className={`tag ${row.reversible ? 'ok' : ''}`}>
                      {row.reversible ? t('app.yes') : t('app.no')}
                    </span>
                  </td>
                  <td>
                    {row.detail && (
                      <button className="btn sm"
                        onClick={() => setExpanded(expanded === row.id ? null : row.id)}>
                        {expanded === row.id ? t('app.hide') : t('app.details')}
                      </button>
                    )}
                  </td>
                </tr>
                {expanded === row.id && (
                  <tr>
                    <td colSpan={7}>
                      <pre className="mono"
                        style={{ margin: 0, direction: 'ltr', overflow: 'auto' }}>
                        {JSON.stringify(row.detail, null, 2)}
                      </pre>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>

        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className="btn sm" disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}>{t('app.previous')}</button>
          <span className="muted">{offset + 1}–{Math.min(offset + PAGE, total)}</span>
          <button className="btn sm" disabled={offset + PAGE >= total}
            onClick={() => setOffset(offset + PAGE)}>{t('app.next')}</button>
        </div>
      </div>
    </>
  )
}
