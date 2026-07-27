import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { subscribeJobs, type JobEvent } from './api/client'
import LanguageSwitcher from './components/LanguageSwitcher'
import { useI18n, type TranslationKey } from './i18n'
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

export default function App() {
  const { t } = useI18n()
  const [active, setActive] = useState<JobEvent | null>(null)

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
