import { useEffect, useState, type FormEvent } from 'react'
import { api } from '../api/client'
import LanguageSwitcher from '../components/LanguageSwitcher'
import { useI18n } from '../i18n'

const MIN_PASSWORD_LENGTH = 10
const MIN_USERNAME_LENGTH = 3

type AuthStatus = { configured: boolean }

/**
 * One screen for both cases.
 *
 * On a fresh install there is no account at all: Cadenza generates no password
 * and writes no credential to disk, so the first person to open it creates the
 * username and password themselves. Once an account exists this is the sign-in
 * form. `/auth/status` is what decides which.
 */
export default function Login({ onSignedIn }: { onSignedIn: () => void }) {
  const { t } = useI18n()
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.get<AuthStatus>('/auth/status').then(setStatus)
      // If the status call fails we cannot know, and offering to create an
      // account when one may already exist is the worse guess.
      .catch(() => setStatus({ configured: true }))
  }, [])

  const isSetup = status !== null && !status.configured

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)

    if (isSetup) {
      if (username.trim().length < MIN_USERNAME_LENGTH) {
        setError(t('auth.usernameTooShort', { min: MIN_USERNAME_LENGTH })); return
      }
      if (password.length < MIN_PASSWORD_LENGTH) {
        setError(t('auth.tooShort', { min: MIN_PASSWORD_LENGTH })); return
      }
      if (password !== confirm) { setError(t('auth.mismatch')); return }
    }

    setBusy(true)
    try {
      if (isSetup) {
        await api.post('/auth/setup', { username: username.trim(), password })
      } else {
        await api.post('/auth/login', { username: username.trim(), password })
      }
      setPassword(''); setConfirm('')
      onSignedIn()
    } catch (err) {
      const message = (err as Error).message
      // 409 during setup means somebody claimed this install first; reload so
      // the form switches to sign-in rather than repeating a request that
      // cannot now succeed.
      if (isSetup && message.startsWith('409')) {
        setStatus({ configured: true })
        setError(t('auth.alreadyConfigured'))
      } else {
        setError(message.startsWith('400') ? message : t('auth.invalid'))
      }
    } finally {
      setBusy(false)
    }
  }

  const canSubmit = isSetup
    ? Boolean(username.trim() && password && confirm)
    : Boolean(password)

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

        {isSetup && <div className="banner">{t('auth.setupIntro')}</div>}
        {error && <div className="banner danger">{error}</div>}

        <label className="field">
          <span>{t('auth.username')}</span>
          <input
            type="text"
            value={username}
            autoFocus
            autoComplete={isSetup ? 'username' : 'username'}
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>

        <label className="field">
          <span>{t('auth.password')}</span>
          <input
            type="password"
            value={password}
            autoComplete={isSetup ? 'new-password' : 'current-password'}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {isSetup && (
          <label className="field">
            <span>{t('auth.confirmPassword')}</span>
            <input
              type="password"
              value={confirm}
              autoComplete="new-password"
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
        )}

        <button className="btn primary" type="submit"
          disabled={busy || !canSubmit} style={{ width: '100%' }}>
          {busy
            ? (isSetup ? t('auth.creating') : t('auth.signingIn'))
            : (isSetup ? t('auth.createAccount') : t('auth.signIn'))}
        </button>

        <p className="muted" style={{ fontSize: 12.5, marginTop: 16 }}>
          {isSetup ? t('auth.setupNote') : t('auth.firstRun')}
        </p>

        <div style={{ marginTop: 18 }}>
          <LanguageSwitcher />
        </div>
      </form>
    </div>
  )
}

/** Changing the password later, from Settings. */
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

        <button className="btn primary" type="submit" disabled={busy}
          style={{ width: '100%' }}>
          {t('auth.changeSubmit')}
        </button>
      </form>
    </div>
  )
}
