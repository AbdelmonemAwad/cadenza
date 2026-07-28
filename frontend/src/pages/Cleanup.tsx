import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n } from '../i18n'

type Category = { key: string; what: string; detail: string }
type Item = {
  category: string; path: string; size_bytes: number
  reason: string; database_only: boolean
}
type ScanResult = {
  categories: string[]
  found: number
  by_category: Record<string, { count: number; bytes: number }>
  refused: string[]
  items: Item[]
}

function bytes(value: number): string {
  if (!value) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)))
  return `${(value / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`
}

/**
 * Tidying up, with the preview first and nothing selected by default.
 *
 * Nothing is ticked when the page opens, and the run button stays disabled
 * until something is: the categories do very different things — deleting a
 * thumbnail cache is not the same as quarantining someone's hand-written
 * lyrics — so "select all and go" is not offered as the easy path.
 */
export default function Cleanup() {
  const { t, n } = useI18n()
  const [categories, setCategories] = useState<Category[]>([])
  const [note, setNote] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(new Set())
  const [result, setResult] = useState<ScanResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    api.get<{ categories: Category[]; note: string }>('/cleanup/categories')
      .then((r) => { setCategories(r.categories); setNote(r.note) })
      .catch((e) => setMessage((e as Error).message))
  }, [])

  const toggle = (key: string) => setChosen((current) => {
    const next = new Set(current)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  const preview = async () => {
    setBusy(true); setMessage(null)
    try {
      const params = new URLSearchParams()
      chosen.forEach((c) => params.append('category', c))
      setResult(await api.get<ScanResult>(`/cleanup/scan?${params}`))
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const run = async () => {
    setBusy(true); setMessage(null)
    try {
      const r = await api.post<{ job_id: number }>('/cleanup', {
        categories: [...chosen], dry_run: false,
      })
      setMessage(t('cleanup.started', { id: String(r.job_id) }))
      setResult(null)
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('cleanup.title')}</h1>
        <p>{t('cleanup.subtitle')}</p>
      </div>
      {message && <div className="banner">{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('cleanup.choose')}</h3>
        <p className="muted">{note}</p>
        {categories.map((c) => (
          <label className="check" key={c.key} style={{ alignItems: 'flex-start' }}>
            <input type="checkbox" checked={chosen.has(c.key)}
              onChange={() => toggle(c.key)} />
            <span>
              <strong>{c.what}</strong>
              <br />
              <span className="muted" style={{ fontSize: 12 }}>{c.detail}</span>
            </span>
          </label>
        ))}

        <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
          <button className="btn" disabled={busy || !chosen.size} onClick={preview}>
            {t('cleanup.preview')}
          </button>
          {/* Only after a preview, and only if it found something. The run
              button is not a shortcut past looking at the list. */}
          <button className="btn danger" disabled={busy || !result || !result.found}
            onClick={run}>
            {t('cleanup.run')}
          </button>
          <span className="muted">{t('cleanup.previewFirst')}</span>
        </div>
      </div>

      {result && (
        <div className="card">
          <h3>{t('cleanup.found', { count: n(result.found) })}</h3>

          {!result.found && <p className="muted">{t('cleanup.nothingToDo')}</p>}

          {!!result.refused.length && (
            <div className="banner">
              {t('cleanup.refused')}
              <ul style={{ margin: '6px 0 0' }}>
                {result.refused.map((r) => (
                  <li key={r} className="mono" style={{ fontSize: 12 }}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {!!Object.keys(result.by_category).length && (
            <table style={{ marginBottom: 12 }}>
              <thead>
                <tr>
                  <th>{t('cleanup.colCategory')}</th>
                  <th>{t('cleanup.colCount')}</th>
                  <th>{t('cleanup.colSize')}</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(result.by_category).map(([key, v]) => (
                  <tr key={key}>
                    <td>{categories.find((c) => c.key === key)?.what ?? key}</td>
                    <td className="mono">{n(v.count)}</td>
                    <td className="mono">{bytes(v.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {!!result.items.length && (
            <>
              <div className="stat-label">{t('cleanup.whatExactly')}</div>
              <div style={{ maxHeight: 320, overflow: 'auto' }}>
                <table>
                  <tbody>
                    {result.items.map((item) => (
                      <tr key={item.path}>
                        <td className="mono truncate" style={{ maxWidth: 460,
                          direction: 'ltr' }} title={item.path}>
                          {item.path}
                        </td>
                        <td className="muted" style={{ fontSize: 12 }}>{item.reason}</td>
                        <td className="mono">
                          {item.database_only ? t('cleanup.rowOnly') : bytes(item.size_bytes)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </>
  )
}
