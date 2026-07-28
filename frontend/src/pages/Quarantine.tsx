import { useEffect, useState } from 'react'
import { api, humanBytes } from '../api/client'
import { useI18n } from '../i18n'

type Item = {
  id: number; original_path: string; quarantine_path: string
  size_bytes: number; reason: string; group_id: number | null
  moved_at: string; purge_after: string | null; restored: boolean
}
type Stats = {
  items: number; bytes: number; retention_days: number; hard_delete_allowed: boolean
}

const PAGE = 100

export default function Quarantine() {
  const { t, n, d } = useI18n()
  const [items, setItems] = useState<Item[]>([])
  const [stats, setStats] = useState<Stats | null>(null)
  const [showRestored, setShowRestored] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)

  // The endpoint has always returned a total and accepted an offset. The page
  // asked for 200 items, threw the total away and never sent an offset -- so
  // anything past the two-hundredth quarantined file could not be seen, and
  // therefore could not be restored, from the interface at all.
  const load = () => {
    const params = new URLSearchParams({
      restored: String(showRestored), limit: String(PAGE), offset: String(offset),
    })
    api.get<{ total: number; items: Item[] }>(`/quarantine?${params}`)
      .then((r) => { setItems(r.items); setTotal(r.total) })
      .catch((e) => setMessage(e.message))
    api.get<Stats>('/quarantine/stats').then(setStats).catch(() => {})
  }
  useEffect(() => { load() }, [showRestored, offset])
  useEffect(() => { setOffset(0) }, [showRestored])

  const restore = async (id: number) => {
    try {
      const r = await api.post<{ restored_to: string }>(`/quarantine/${id}/restore`)
      setMessage(t('quarantine.restoredTo', { path: r.restored_to }))
      load()
    } catch (e) { setMessage((e as Error).message) }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('quarantine.title')}</h1>
        <p>
          {stats && t('quarantine.summary', {
            count: n(stats.items), size: humanBytes(stats.bytes),
          })}
        </p>
        <div className="spacer" />
        <label className="check" style={{ margin: 0 }}>
          <input type="checkbox" checked={showRestored}
            onChange={(e) => setShowRestored(e.target.checked)} />
          {t('quarantine.showRestored')}
        </label>
      </div>

      <div className="banner ok">
        {t('quarantine.safetyNotice', { days: stats?.retention_days ?? 30 })}
        {stats && !stats.hard_delete_allowed && t('quarantine.hardDeleteOff')}
      </div>
      {message && <div className="banner warn">{message}</div>}

      <div className="card">
        {!items.length && <div className="empty">{t('quarantine.noItems')}</div>}
        {!!items.length && (
          <table>
            <thead>
              <tr>
                <th>{t('quarantine.colOriginalPath')}</th>
                <th>{t('quarantine.colReason')}</th>
                <th>{t('library.colSize')}</th>
                <th>{t('quarantine.colMovedAt')}</th>
                <th>{t('quarantine.colPurgeAfter')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.id}>
                  <td className="mono truncate" style={{ maxWidth: 460 }}
                    title={item.original_path}>{item.original_path}</td>
                  <td><span className="tag">{item.reason}</span></td>
                  <td className="muted">{humanBytes(item.size_bytes)}</td>
                  <td className="muted">{d(item.moved_at)}</td>
                  <td className="muted">{d(item.purge_after)}</td>
                  <td>
                    {item.restored
                      ? <span className="tag ok">{t('quarantine.restored')}</span>
                      : (
                        <button className="btn sm primary" onClick={() => restore(item.id)}>
                          {t('quarantine.restore')}
                        </button>
                      )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {total > PAGE && (
          <div className="toolbar" style={{ marginTop: 12, marginBottom: 0 }}>
            <button className="btn sm" disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              {t('app.previous')}
            </button>
            <span className="muted">
              {offset + 1}–{Math.min(offset + PAGE, total)} {t('app.of')} {n(total)}
            </span>
            <button className="btn sm" disabled={offset + PAGE >= total}
              onClick={() => setOffset(offset + PAGE)}>
              {t('app.next')}
            </button>
          </div>
        )}
      </div>
    </>
  )
}
