import { Fragment, useEffect, useState } from 'react'
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
type Imported = {
  id: number; external_id: string | null; name: string
  matched: number; unmatched: number; synced_at: string | null
  unmatched_items: { title: string | null; artist: string | null; url?: string | null }[]
}

declare global {
  interface Window { MusicKit?: any }
}

const MUSICKIT_CDN = 'https://js-cdn.music.apple.com/musickit/v3/musickit.js'
const MUSICKIT_TIMEOUT_MS = 20_000

let musicKitLoading: Promise<void> | null = null

/** Resolves once `window.MusicKit` exists.
 *
 *  MusicKit JS announces itself with a `musickitloaded` event on `document`.
 *  The script element's own `load` event fires before that, when the global
 *  may not be there yet. This used to append the script and then wait a fixed
 *  400 ms, which was a guess: on a slow link the guess lost, `window.MusicKit`
 *  was undefined, and linking failed with a message that did not mention the
 *  cause. It now waits for the event, and gives up with a reason rather than
 *  silently. The promise is shared so a second click while the first load is
 *  in flight does not append the script twice. */
function loadMusicKit(messages: { timeout: string; failed: string }): Promise<void> {
  if (window.MusicKit) return Promise.resolve()
  if (musicKitLoading) return musicKitLoading
  musicKitLoading = new Promise<void>((resolve, reject) => {
    const finish = (err?: Error) => {
      clearTimeout(timer)
      document.removeEventListener('musickitloaded', onReady)
      musicKitLoading = null
      if (err) reject(err); else resolve()
    }
    const onReady = () => finish()
    const timer = setTimeout(() => finish(new Error(messages.timeout)), MUSICKIT_TIMEOUT_MS)
    document.addEventListener('musickitloaded', onReady)
    const el = document.createElement('script')
    el.src = MUSICKIT_CDN
    el.async = true
    // If the global is already there when the script's own load event fires,
    // there is nothing left to wait for.
    el.onload = () => { if (window.MusicKit) finish() }
    el.onerror = () => finish(new Error(messages.failed))
    document.head.appendChild(el)
  })
  return musicKitLoading
}

export default function AppleMusic() {
  const { t, n, d } = useI18n()
  const [status, setStatus] = useState<Status | null>(null)
  const [playlists, setPlaylists] = useState<Playlist[]>([])
  const [imported, setImported] = useState<Imported[]>([])
  const [showUnmatched, setShowUnmatched] = useState<number | null>(null)
  const [match, setMatch] = useState<MatchResult | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => api.get<Status>('/apple/status').then(setStatus)
    .catch((e) => setMessage(e.message))
  const loadImported = () => api.get<{ items: Imported[] }>('/apple/playlists/imported')
    .then((r) => setImported(r.items))
    .catch((e) => setMessage(e.message))
  useEffect(() => { load(); loadImported() }, [])

  /** Links the account through MusicKit JS: Apple's own sign-in window handles
      the credentials, and we only ever receive the resulting user token. */
  const linkAccount = async () => {
    setBusy(true); setMessage(null)
    try {
      const { token } = await api.get<{ token: string }>('/apple/developer-token')
      await loadMusicKit({
        timeout: t('apple.musickitTimeout'), failed: t('apple.musickitLoadFailed'),
      })
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
      // "Matched tracks" comes from /status and was not re-read after a run,
      // so the card kept the count from page load while the table below it
      // showed the matches that had just been written.
      load()
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const loadPlaylists = async () => {
    setBusy(true)
    try {
      const r = await api.get<{ items: Playlist[] }>('/apple/playlists')
      setPlaylists(r.items)
    } catch (e) { setMessage((e as Error).message) } finally { setBusy(false) }
  }

  const importPlaylist = async (p: Playlist) => {
    setBusy(true)
    try {
      const r = await api.post<{ matched: number; unmatched: number }>(
        `/apple/playlists/${p.id}/import`, { name: p.name })
      setMessage(t('apple.importResult', { matched: r.matched, unmatched: r.unmatched }))
      loadImported()
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
                      onClick={() => importPlaylist(p)}>
                      {t('apple.importAndMatch')}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* The import stored its result -- which tracks matched, which entries
          the library lacks and where to find them on Apple Music -- and no
          page read it. A one-line count was all that ever reached the user. */}
      <div className="card" style={{ marginTop: 16 }}>
        <h3>{t('apple.importedTitle')}</h3>
        {!imported.length && <p className="muted">{t('apple.noImports')}</p>}
        {!!imported.length && (
          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>{t('apple.colName')}</th>
                <th>{t('apple.colMatched')}</th>
                <th>{t('apple.colUnmatched')}</th>
                <th>{t('apple.colImported')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {imported.map((p) => (
                <Fragment key={p.id}>
                  <tr>
                    <td>{p.name}</td>
                    <td>{n(p.matched)}</td>
                    <td>{n(p.unmatched)}</td>
                    <td className="muted">{d(p.synced_at)}</td>
                    <td>
                      {p.unmatched > 0 && (
                        <button className="btn sm"
                          onClick={() => setShowUnmatched(showUnmatched === p.id ? null : p.id)}>
                          {showUnmatched === p.id ? t('app.hide') : t('app.details')}
                        </button>
                      )}
                    </td>
                  </tr>
                  {showUnmatched === p.id && (
                    <tr>
                      <td colSpan={5}>
                        <p className="muted">{t('apple.unmatchedHint')}</p>
                        <table>
                          <thead>
                            <tr>
                              <th>{t('apple.colApple')}</th>
                              <th>{t('apple.colArtist')}</th>
                              <th />
                            </tr>
                          </thead>
                          <tbody>
                            {p.unmatched_items.map((u, i) => (
                              <tr key={i}>
                                <td className="truncate" style={{ maxWidth: 300 }}>
                                  {u.title ?? '—'}
                                </td>
                                <td className="truncate" style={{ maxWidth: 200 }}>
                                  {u.artist ?? '—'}
                                </td>
                                <td>
                                  {u.url && (
                                    <a className="btn sm" href={u.url} target="_blank"
                                      rel="noreferrer">
                                      {t('apple.open')}
                                    </a>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
