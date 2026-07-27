import { useCallback, useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, subscribeJobs, UNAUTHORIZED_EVENT, type JobEvent } from './api/client'
import LanguageSwitcher from './components/LanguageSwitcher'
import { useI18n, type TranslationKey } from './i18n'
import Login from './pages/Login'
import AppleMusic from './pages/AppleMusic'
import Convert from './pages/Convert'
import Dashboard from './pages/Dashboard'
import Duplicates from './pages/Duplicates'
import Jobs from './pages/Jobs'
import Library from './pages/Library'
import Logs from './pages/Logs'
import Quarantine from './pages/Quarantine'
import Settings from './pages/Settings'

const NAV: { to: string; key: TranslationKey; icon: string }[] = [
  { to: '/dashboard', key: 'nav.dashboard', icon: '▦' },
  { to: '/library', key: 'nav.library', icon: '♪' },
  { to: '/duplicates', key: 'nav.duplicates', icon: '⧉' },
  { to: '/convert', key: 'nav.convert', icon: '⇄' },
  { to: '/quarantine', key: 'nav.quarantine', icon: '⌫' },
  { to: '/apple', key: 'nav.apple', icon: '' },
  { to: '/jobs', key: 'nav.jobs', icon: '⏱' },
  { to: '/logs', key: 'nav.logs', icon: '≡' },
  { to: '/settings', key: 'nav.settings', icon: '⚙' },
]

type Session = 'checking' | 'anonymous' | 'ready'

export default function App() {
  const { t } = useI18n()
  const [active, setActive] = useState<JobEvent | null>(null)
  const [session, setSession] = useState<Session>('checking')

  // A cheap authenticated call decides whether a session cookie is still
  // valid; /auth/status only reports whether a password exists at all.
  const probeSession = useCallback(() => {
    api.get('/settings/health')
      .then(() => setSession('ready'))
      .catch((err: Error) => {
        // 403 means the session is valid but scoped to the password change.
        // Only a 401 -- or an outright failure to reach the API -- should send
        // the user back to the sign-in screen.
        setSession('anonymous')
      })
  }, [])

  useEffect(() => { probeSession() }, [probeSession])

  useEffect(() => {
    const onUnauthorized = () => setSession('anonymous')
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  useEffect(() => subscribeJobs((e) => {
    if (e.type === 'job.finished') {
      setActive(null)
      // Pages listen for this instead of polling on a timer.
      window.dispatchEvent(new CustomEvent('cadenza:job-finished', { detail: e }))
    } else {
      setActive(e)
    }
  }), [])

  const percent = active?.total
    ? Math.round(((active.processed ?? 0) / active.total) * 100)
    : 0

  // Signing in and creating the account both land in the same place: there is
  // no forced password change any more, because the password was never
  // generated -- the user chose it.
  const signIn = () => setSession('ready')

  if (session === 'checking') return <div className="empty">{t('app.loading')}</div>
  if (session === 'anonymous') return <Login onSignedIn={signIn} />

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">♫</div>
          <div>
            <div className="brand-name">{t('app.name')}</div>
            <div className="brand-sub">{t('app.tagline')}</div>
          </div>
        </div>

        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to}
            className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}>
            <span aria-hidden>{n.icon}</span>{t(n.key)}
          </NavLink>
        ))}

        <div className="sidebar-footer">
          <button className="btn sm" style={{ width: '100%', marginBottom: 8 }}
            onClick={async () => {
              // Only claim to be signed out once the server has actually
              // cleared the cookie. Returning to the login screen regardless
              // would leave a valid 14-day session in the browser while
              // looking like a successful sign-out.
              try {
                await api.post('/auth/logout')
                setSession('anonymous')
              } catch {
                alert(t('auth.signOutFailed'))
              }
            }}>
            {t('auth.signOut')}
          </button>
          <LanguageSwitcher />
        </div>
      </aside>

      <main className="main">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/library" element={<Library />} />
          <Route path="/duplicates" element={<Duplicates />} />
          <Route path="/convert" element={<Convert />} />
          <Route path="/quarantine" element={<Quarantine />} />
          <Route path="/apple" element={<AppleMusic />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/logs" element={<Logs />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<div className="empty">{t('app.notFound')}</div>} />
        </Routes>
      </main>

      {active && active.type !== 'ping' && (
        <div className="job-strip">
          <span className="tag blue">{t('jobStrip.job', { id: active.job_id ?? '?' })}</span>
          <span>{active.kind ?? active.message ?? t('jobStrip.running')}</span>
          <div className="bar"><i style={{ width: `${percent}%` }} /></div>
          <span className="muted">{active.processed ?? 0} / {active.total ?? '?'}</span>
        </div>
      )}
    </div>
  )
}
