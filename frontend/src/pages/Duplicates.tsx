import { useEffect, useState } from 'react'
import { api, humanBytes, humanDuration } from '../api/client'
import { useI18n, type TranslationKey } from '../i18n'

type Action = 'keep' | 'quarantine' | 'skip'

type Member = {
  track_id: number; path: string; codec: string | null; bitrate: number | null
  lossless: boolean; duration: number | null; size_bytes: number
  score: number; breakdown: Record<string, number> | null
  reason: string | null; action: Action; applied: boolean
}
type Group = {
  id: number; kind: string; confidence: number; member_count: number
  reclaimable_bytes: number; members: Member[]
}

const KIND_KEYS: Record<string, TranslationKey> = {
  exact_file: 'duplicates.kindExactFile',
  exact_audio: 'duplicates.kindExactAudio',
  acoustic: 'duplicates.kindAcoustic',
  metadata: 'duplicates.kindMetadata',
}

const ACTION_KEYS: Record<Action, TranslationKey> = {
  keep: 'duplicates.actionKeep',
  quarantine: 'duplicates.actionQuarantine',
  skip: 'duplicates.actionSkip',
}

export default function Duplicates() {
  const { t, n } = useI18n()
  const [groups, setGroups] = useState<Group[]>([])
  const [total, setTotal] = useState(0)
  const [kind, setKind] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<number | null>(null)

  const load = () => api
    .get<{ total: number; items: Group[] }>(
      `/duplicates/groups?limit=100${kind ? `&kind=${kind}` : ''}`)
    .then((r) => { setGroups(r.items); setTotal(r.total) })
    .catch((e) => setMessage(e.message))

  useEffect(() => {
    load()
    const onDone = () => load()
    window.addEventListener('cadenza:job-finished', onDone)
    return () => window.removeEventListener('cadenza:job-finished', onDone)
  }, [kind])

  const analyze = async () => {
    setBusy(true); setMessage(null)
    try {
      await api.post('/duplicates/analyze', {})
      setMessage(t('duplicates.analysisStarted'))
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const apply = async (dryRun: boolean, groupIds?: number[]) => {
    if (!dryRun && !confirm(t('duplicates.confirmApply'))) return
    setBusy(true); setMessage(null)
    try {
      await api.post('/duplicates/apply', { group_ids: groupIds ?? null, dry_run: dryRun })
      setMessage(dryRun ? t('duplicates.previewStarted') : t('duplicates.applyStarted'))
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const setAction = async (group: Group, trackId: number, action: Action) => {
    // Promoting a copy to "keep" demotes the previous keeper, since the API
    // rejects a group with zero or several keepers.
    const overrides = group.members.map((m) => ({
      track_id: m.track_id,
      action: m.track_id === trackId
        ? action
        : (action === 'keep' && m.action === 'keep' ? 'quarantine' : m.action),
    }))
    try {
      await api.patch(`/duplicates/groups/${group.id}/members`, overrides)
      load()
    } catch (e) { setMessage((e as Error).message) }
  }

  const totalReclaim = groups.reduce((a, g) => a + g.reclaimable_bytes, 0)

  return (
    <>
      <div className="page-head">
        <h1>{t('duplicates.title')}</h1>
        <p>{t('duplicates.summary', { groups: total, size: humanBytes(totalReclaim) })}</p>
        <div className="spacer" />
        <button className="btn" disabled={busy} onClick={analyze}>
          {t('duplicates.reanalyze')}
        </button>
        <button className="btn" disabled={busy || !groups.length} onClick={() => apply(true)}>
          {t('duplicates.dryRun')}
        </button>
        <button className="btn danger" disabled={busy || !groups.length}
          onClick={() => apply(false)}>
          {t('duplicates.quarantineAll')}
        </button>
      </div>

      <div className="banner">{t('duplicates.safetyNotice')}</div>
      {message && <div className="banner warn">{message}</div>}

      <div className="toolbar">
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: 260 }}>
          <option value="">{t('duplicates.allKinds')}</option>
          {Object.entries(KIND_KEYS).map(([value, key]) => (
            <option key={value} value={value}>{t(key)}</option>
          ))}
        </select>
      </div>

      {!groups.length && <div className="empty">{t('duplicates.noGroups')}</div>}

      {groups.map((g) => (
        <div className="card" key={g.id} style={{ marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <span className="tag blue">{KIND_KEYS[g.kind] ? t(KIND_KEYS[g.kind]) : g.kind}</span>
            <span className="tag">
              {t('duplicates.confidence', { percent: Math.round(g.confidence * 100) })}
            </span>
            <span className="muted">{t('duplicates.memberCount', { count: g.member_count })}</span>
            <span className="tag warn">
              {t('duplicates.saves', { size: humanBytes(g.reclaimable_bytes) })}
            </span>
            <div className="spacer" />
            <button className="btn sm" onClick={() => setExpanded(expanded === g.id ? null : g.id)}>
              {expanded === g.id ? t('app.hide') : t('app.details')}
            </button>
            <button className="btn sm"
              onClick={() => api.post(`/duplicates/groups/${g.id}/ignore`).then(load)}>
              {t('duplicates.ignore')}
            </button>
            <button className="btn sm danger" onClick={() => apply(false, [g.id])}>
              {t('duplicates.quarantineNow')}
            </button>
          </div>

          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>{t('duplicates.colDecision')}</th>
                <th>{t('duplicates.colPath')}</th>
                <th>{t('library.colFormat')}</th>
                <th>{t('duplicates.colBitrate')}</th>
                <th>{t('library.colDuration')}</th>
                <th>{t('library.colSize')}</th>
                <th>{t('duplicates.colScore')}</th>
                <th>{t('duplicates.colReason')}</th>
              </tr>
            </thead>
            <tbody>
              {g.members.map((m) => (
                <tr key={m.track_id}>
                  <td>
                    <select value={m.action} style={{ width: 118 }}
                      onChange={(e) => setAction(g, m.track_id, e.target.value as Action)}>
                      {(Object.keys(ACTION_KEYS) as Action[]).map((a) => (
                        <option key={a} value={a}>{t(ACTION_KEYS[a])}</option>
                      ))}
                    </select>
                  </td>
                  <td className="mono truncate" title={m.path}>{m.path}</td>
                  <td><span className={`tag ${m.lossless ? 'ok' : ''}`}>{m.codec ?? '—'}</span></td>
                  <td>{m.bitrate ? `${Math.round(m.bitrate / 1000)}k` : '—'}</td>
                  <td>{humanDuration(m.duration)}</td>
                  <td className="muted">{humanBytes(m.size_bytes)}</td>
                  <td><strong>{n(Math.round(m.score * 1000) / 10)}</strong></td>
                  <td className="muted truncate" style={{ maxWidth: 240 }}>{m.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {expanded === g.id && (
            <div style={{ marginTop: 12, overflowX: 'auto' }}>
              <h3>{t('duplicates.scoreBreakdown')}</h3>
              <table className="mono">
                <thead>
                  <tr>
                    <th>{t('duplicates.colPath')}</th>
                    {Object.keys(g.members[0]?.breakdown ?? {}).map((k) => <th key={k}>{k}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {g.members.map((m) => (
                    <tr key={m.track_id}>
                      <td className="truncate" style={{ maxWidth: 300 }}>
                        {m.path.split('/').pop()}
                      </td>
                      {Object.values(m.breakdown ?? {}).map((v, i) => (
                        <td key={i}>{typeof v === 'number' ? v.toFixed(2) : String(v)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </>
  )
}
