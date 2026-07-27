import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import LanguageSwitcher from '../components/LanguageSwitcher'
import { useI18n } from '../i18n'

const MIN_PASSWORD_LENGTH = 10

type AuthStatus = { configured: boolean }

export default function Login(
  { onSignedIn }: { onSignedIn: (mustChangePassword: boolean) => void },
) {
  const { t } = useI18n()
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get<AuthStatus>('/auth/status').then(setStatus)
      .catch(() => setStatus({ configured: false }))
  }, [])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      // The login response is the only place that reports whether the
      // generated password is still in force; /auth/status deliberately does
      // not tell unauthenticated callers.
      const result = await api.post<{ must_change_password: boolean }>(
        '/auth/login', { password })
      setPassword('')
      onSignedIn(Boolean(result.must_change_password))
    } catch (err) {
      const message = (err as Error).message
      // 503 means the container has not finished first-run initialisation.
      setError(message.startsWith('503') ? t('auth.notInitialised') : t('auth.invalid'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand" style={{ padding: 0, marginBottom: 18 }}>
          <div className="brand-mark">♫</div>
          <div>
            <div className="brand-name">{t('auth.title')}</div>
            <div className="brand-sub">{t('auth.subtitle')}</div>
          </div>
        </div>

        {status && !status.configured && (
          <div className="banner warn">{t('auth.notInitialised')}</div>
        )}
        {error && <div className="banner danger">{error}</div>}

        <label className="field">
          <span>{t('auth.password')}</span>
          <input
            type="password"
            value={password}
            autoFocus
            autoComplete="current-password"
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        <button className="btn primary" type="submit"
          disabled={busy || !password} style={{ width: '100%' }}>
          {busy ? t('auth.signingIn') : t('auth.signIn')}
        </button>

        <p className="muted" style={{ fontSize: 12.5, marginTop: 16 }}>
          {t('auth.firstRun')}
        </p>

        <div style={{ marginTop: 18 }}>
          <LanguageSwitcher />
        </div>
      </form>
    </div>
  )
}

/** Shown after signing in with the generated password, before anything else. */
export function ChangePassword({ onDone }: { onDone: () => void }) {
  const { t } = useI18n()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (next !== confirm) { setError(t('auth.mismatch')); return }
    if (next.length < MIN_PASSWORD_LENGTH) {
      setError(t('auth.tooShort', { min: MIN_PASSWORD_LENGTH })); return
    }
    if (next === current) { setError(t('auth.sameAsOld')); return }

    setBusy(true)
    setError(null)
    try {
      await api.post('/auth/password', { current_password: current, new_password: next })
      onDone()
    } catch (err) {
      setError((err as Error).message.startsWith('401')
        ? t('auth.invalid') : (err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={submit}>
        <h3 style={{ marginTop: 0 }}>{t('auth.changeTitle')}</h3>
        <div className="banner warn">{t('auth.changeRequired')}</div>
        {error && <div className="banner danger">{error}</div>}

        <label className="field">
          <span>{t('auth.currentPassword')}</span>
          <input type="password" value={current} autoComplete="current-password"
            onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="field">
          <span>{t('auth.newPassword')}</span>
          <input type="password" value={next} autoComplete="new-password"
            onChange={(e) => setNext(e.target.value)} />
        </label>
        <label className="field">
          <span>{t('auth.confirmPassword')}</span>
          <input type="password" value={confirm} autoComplete="new-password"
            onChange={(e) => setConfirm(e.target.value)} />
        </label>

        {/* No "later" escape hatch: the server issues a session scoped to
            these endpoints until the password is changed, so skipping it would
            only produce 403s everywhere else. */}
        <button className="btn primary" type="submit" disabled={busy}
          style={{ width: '100%' }}>
          {t('auth.changeSubmit')}
        </button>
      </form>
    </div>
  )
}
