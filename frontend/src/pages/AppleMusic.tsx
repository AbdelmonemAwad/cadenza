import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n } from '../i18n'

type Status = {
  configured: boolean; user_linked: boolean
  storefront: string; matched_tracks: number
}
type Playlist = {
  id: string; name: string; description: string | null; can_edit: boolean
}
type MatchResult = {
  checked: number; matched: number; unmatched: number
  items: {
    track_id: number; title: string; apple_title: string; apple_artist: string
    confidence: number; url?: string
  }[]
}

declare global {
  interface Window { MusicKit?: any }
}

const MUSICKIT_CDN = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js'

export default function AppleMusic() {
  const { t, n } = useI18n()
  const [status, setStatus] = useState<Status | null>(null)
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [match, setMatch] = useState<MatchResult | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.get<Status>('/apple/status').then(setStatus)
    .catch((e) => setMessage(e.message))
  useEffect(() => { load() }, [])

  /** Links the account through MusicKit JS: Apple's own sign-in window handles
      the credentials, and we only ever receive the resulting user token. */
  const linkAccount = async () => {
    setBusy(true); setMessage(null)
    try {
      const { token } = await api.get<{ token: string }>('/apple/developer-token')
      if (!window.MusicKit) {
        await new Promise<void>((resolve, reject) => {
          const el = document.createElement('script')
          el.src = MUSICKIT_CDN
          el.async = true
          el.onload = () => resolve()
          el.onerror = () => reject(new Error('Could not load MusicKit JS'))
          document.head.appendChild(el)
        })
        await new Promise((r) => setTimeout(r, 400))
      }
      await window.MusicKit.configure({
        developerToken: token,
        app: { name: 'Cadenza', build: '1.0.0' },
      })
      const userToken = await window.MusicKit.getInstance().authorize()
      await api.post('/apple/link', { music_user_token: userToken })
      setMessage(t('apple.linkSuccess'))
      load()
    } catch (e) {
      setMessage((e as Error).message)
    } finally { setBusy(false) }
  }

  const runMatch = async () => {
    setBusy(true); setMessage(null)
    try {
      setMatch(await api.post<MatchResult>('/apple/match',
        { limit: 200, write_apple_id: true }))
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const loadPlaylists = async () => {
    setBusy(true)
    try {
      const r = await api.get<{ items: Playlist[] }>('/apple/playlists')
      setPlaylists(r.items)
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const importPlaylist = async (id: string) => {
    setBusy(true)
    try {
      const r = await api.post<{ matched: number; unmatched: number }>(
        `/apple/playlists/${id}/import`)
      setMessage(t('apple.importResult', { matched: r.matched, unmatched: r.unmatched }))
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('apple.title')}</h1>
        <p>{t('apple.subtitle')}</p>
      </div>

      {message && <div className="banner">{message}</div>}
      {status && !status.configured && (
        <div className="banner warn">{t('apple.notConfigured')}</div>
      )}

      <div className="grid cols-3">
        <div className="card">
          <div className="stat-label">{t('apple.configStatus')}</div>
          <div className={`stat-value ${status?.configured ? 'ok' : 'warn'}`}>
            {status?.configured ? t('apple.ready') : t('apple.incomplete')}
          </div>
          <div className="stat-hint">
            {t('apple.storefront', { code: status?.storefront ?? '—' })}
          </div>
        </div>

        <div className="card">
          <div className="stat-label">{t('apple.accountLink')}</div>
          <div className={`stat-value ${status?.user_linked ? 'ok' : ''}`}>
            {status?.user_linked ? t('apple.linked') : t('apple.notLinked')}
          </div>
          <button className="btn primary sm" style={{ marginTop: 8 }}
            disabled={busy || !status?.configured} onClick={linkAccount}>
            {status?.user_linked ? t('apple.relink') : t('apple.linkAccount')}
          </button>
        </div>

        <div className="card">
          <div className="stat-label">{t('apple.matchedTracks')}</div>
          <div className="stat-value">{n(status?.matched_tracks ?? 0)}</div>
          <button className="btn sm" style={{ marginTop: 8 }}
            disabled={busy || !status?.configured} onClick={runMatch}>
            {t('apple.matchLibrary')}
          </button>
        </div>
      </div>

      {match && (
        <div className="card" style={{ marginTop: 16 }}>
          <h3>{t('apple.matchResult', { matched: match.matched, checked: match.checked })}</h3>
          <table>
            <thead>
              <tr>
                <th>{t('apple.colLocal')}</th>
                <th>{t('apple.colApple')}</th>
                <th>{t('apple.colArtist')}</th>
                <th>{t('apple.colConfidence')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {match.items.slice(0, 60).map((m) => (
                <tr key={m.track_id}>
                  <td className="truncate" style={{ maxWidth: 240 }}>{m.title}</td>
                  <td className="truncate" style={{ maxWidth: 240 }}>{m.apple_title}</td>
                  <td className="truncate" style={{ maxWidth: 180 }}>{m.apple_artist}</td>
                  <td>
                    <span className={`tag ${m.confidence > 0.85 ? 'ok' : 'warn'}`}>
                      {Math.round(m.confidence * 100)}%
                    </span>
                  </td>
                  <td>
                    {m.url && (
                      <a className="btn sm" href={m.url} target="_blank" rel="noreferrer">
                        {t('apple.open')}
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <h3 style={{ margin: 0 }}>{t('apple.playlists')}</h3>
          <div className="spacer" />
          <button className="btn sm" disabled={busy || !status?.user_linked}
            onClick={loadPlaylists}>
            {t('apple.loadPlaylists')}
          </button>
        </div>
        {!status?.user_linked && <p className="muted">{t('apple.linkFirst')}</p>}
        {!!playlists.length && (
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>{t('apple.colName')}</th>
                <th>{t('apple.colDescription')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {playlists.map((p) => (
                <tr key={p.id}>
                  <td>{p.name}</td>
                  <td className="muted truncate" style={{ maxWidth: 380 }}>
                    {p.description ?? '—'}
                  </td>
                  <td>
                    <button className="btn sm" disabled={busy}
                      onClick={() => importPlaylist(p.id)}>
                      {t('apple.importAndMatch')}
                    </button>
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
