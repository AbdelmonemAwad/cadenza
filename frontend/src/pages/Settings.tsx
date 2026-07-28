import { useEffect, useState } from 'react'
import { api } from '../api/client'
import FolderPicker from '../components/FolderPicker'
import { ChangePassword } from './Login'
import { useI18n } from '../i18n'

type Health = {
  tools: { ffmpeg: boolean; fpcalc: boolean; note: string | null }
  paths: Record<string, { path: string; exists: boolean; writable: boolean }>
  providers: Record<string, boolean>
  safety: {
    dry_run_default: boolean; hard_delete_allowed: boolean
    quarantine_retention_days: number
  }
}

const SECRET_FIELDS = ['acoustid_api_key', 'discogs_token', 'lastfm_api_key'] as const
const TEMPLATE_FIELDS = '{albumartist} {artist} {album} {year} {track} {disc} {title} {genre}'

type Credential = { description: string; present: boolean; path: string; size: number }

/**
 * Upload a credential file rather than asking for a path to one.
 *
 * The Apple Music signing key had to be copied onto the NAS by hand, into a
 * folder the user had to find for themselves — and on the package it could not
 * work at all, because the default path was the container's layout. Cadenza
 * now stores it in its own data folder, 0600, and never reads it back out.
 */
function CredentialField({ kind, label, hint }: {
  kind: string; label: string; hint: string
}) {
  const { t, n } = useI18n()
  const [state, setState] = useState<Credential | null>(null)
  const [busy, setBusy] = useState(false)
  const [browsing, setBrowsing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = () => api.get<Record<string, Credential>>('/files/credentials')
    .then((all) => setState(all[kind] ?? null))
    .catch(() => {})
  useEffect(() => { load() }, [])

  const send = async (file: File) => {
    setBusy(true); setError(null)
    try {
      await api.upload(`/files/credentials/${kind}`, file)
      await load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  /** Take a copy of a file the user has already put on the NAS. Copied, not
   *  referenced: a referenced file can be moved or renamed afterwards and then
   *  fails somewhere else entirely. */
  const importFrom = async (path: string) => {
    setBusy(true); setError(null)
    try {
      await api.post(`/files/credentials/${kind}/import`, { path })
      await load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const remove = async () => {
    setBusy(true); setError(null)
    try {
      await api.del(`/files/credentials/${kind}`)
      await load()
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="field" style={{ display: 'block' }}>
      <span>
        {label} — <span className={`tag ${state?.present ? 'ok' : ''}`}>
          {state?.present ? t('settings.configured') : t('settings.notConfigured')}
        </span>
      </span>
      <p className="muted" style={{ fontSize: 12, margin: '4px 0' }}>{hint}</p>
      <div className="toolbar" style={{ marginBottom: 0, flexWrap: 'wrap' }}>
        <input type="file" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); e.target.value = '' }} />
        {/* Two ways in, because both are natural: upload from the machine you
            are sitting at, or pick a file you already copied to the NAS. */}
        <button className="btn sm" disabled={busy} onClick={() => setBrowsing(!browsing)}>
          {t('settings.browse')}
        </button>
        {state?.present && (
          <button className="btn sm danger" disabled={busy} onClick={remove}>
            {t('settings.removeFile')}
          </button>
        )}
      </div>
      {browsing && (
        <FolderPicker credential={kind}
          onPick={(path) => importFrom(path)}
          onClose={() => setBrowsing(false)} />
      )}
      {state?.present && (
        <p className="muted mono truncate" style={{ fontSize: 12, direction: 'ltr' }}>
          {state.path} · {n(state.size)} B
        </p>
      )}
      {error && <p className="banner" style={{ marginTop: 6 }}>{error}</p>}
    </div>
  )
}

export default function Settings() {
  const { t } = useI18n()
  const [config, setConfig] = useState<Record<string, any> | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [patch, setPatch] = useState<Record<string, any>>({})
  const [message, setMessage] = useState<string | null>(null)
  const [passwordChanged, setPasswordChanged] = useState(false)

  const load = () => {
    api.get<Record<string, any>>('/settings').then(setConfig)
      .catch((e) => setMessage(e.message))
    api.get<Health>('/settings/health').then(setHealth).catch(() => {})
  }
  useEffect(() => { load() }, [])

  const set = (key: string, value: unknown) => setPatch((p) => ({ ...p, [key]: value }))
  const value = (key: string) => (key in patch ? patch[key] : config?.[key])

  const save = async () => {
    try {
      await api.patch('/settings', patch)
      setPatch({})
      setMessage(t('settings.saved'))
      load()
    } catch (e) { setMessage((e as Error).message) }
  }

  if (!config) return <div className="empty">{t('app.loading')}</div>

  return (
    <>
      <div className="page-head">
        <h1>{t('settings.title')}</h1>
        <div className="spacer" />
        <button className="btn primary" disabled={!Object.keys(patch).length} onClick={save}>
          {t('settings.saveChanges')}
        </button>
      </div>
      {message && <div className="banner">{message}</div>}

      {health && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h3>{t('settings.systemStatus')}</h3>
          <div className="grid cols-3">
            <div>
              <div className="stat-label">{t('settings.tools')}</div>
              <p>
                <span className={`tag ${health.tools.ffmpeg ? 'ok' : 'danger'}`}>ffmpeg</span>{' '}
                <span className={`tag ${health.tools.fpcalc ? 'ok' : 'danger'}`}>fpcalc</span>
              </p>
              {health.tools.note && <p className="muted">{health.tools.note}</p>}
            </div>
            <div>
              <div className="stat-label">{t('settings.paths')}</div>
              {Object.entries(health.paths).map(([key, info]) => (
                <p key={key} className="mono" style={{ margin: '2px 0' }}>
                  <span className={`tag ${info.writable ? 'ok' : 'danger'}`}>{key}</span>{' '}
                  {info.path}
                </p>
              ))}
            </div>
            <div>
              <div className="stat-label">{t('settings.providers')}</div>
              <p>
                {Object.entries(health.providers).map(([key, ok]) => (
                  <span key={key} className={`tag ${ok ? 'ok' : ''}`}
                    style={{ marginInlineEnd: 4 }}>{key}</span>
                ))}
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="grid cols-2">
        <div className="card">
          <h3>{t('settings.safety')}</h3>
          <label className="check">
            <input type="checkbox" checked={!!value('dry_run_default')}
              onChange={(e) => set('dry_run_default', e.target.checked)} />
            {t('settings.dryRunDefault')}
          </label>
          <label className="check">
            <input type="checkbox" checked={!!value('hard_delete_allowed')}
              onChange={(e) => set('hard_delete_allowed', e.target.checked)} />
            {t('settings.hardDelete')}
          </label>
          <label className="field">
            <span>{t('settings.retentionDays')}</span>
            <input type="number" min="1" max="3650"
              value={value('quarantine_retention_days') ?? 30}
              onChange={(e) => set('quarantine_retention_days', Number(e.target.value))} />
          </label>
        </div>

        <div className="card">
          <h3>{t('settings.dedupEngine')}</h3>
          <label className="check">
            <input type="checkbox" checked={!!value('acoustic_enabled')}
              onChange={(e) => set('acoustic_enabled', e.target.checked)} />
            {t('settings.acousticEnabled')}
          </label>
          <label className="field">
            <span>{t('settings.acousticThreshold')}</span>
            <input type="number" step="0.01" min="0.85" max="0.999"
              value={value('acoustic_match_threshold') ?? 0.9}
              onChange={(e) => set('acoustic_match_threshold', Number(e.target.value))} />
          </label>
          <p className="muted" style={{ fontSize: 12, marginTop: -6 }}>
            {t('settings.acousticThresholdHint')}
          </p>
          <label className="field">
            <span>{t('settings.durationTolerance')}</span>
            <input type="number" min="0" max="120"
              value={value('duration_tolerance_s') ?? 7}
              onChange={(e) => set('duration_tolerance_s', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>{t('settings.fuzzyThreshold')}</span>
            <input type="number" min="50" max="100"
              value={value('title_fuzzy_threshold') ?? 88}
              onChange={(e) => set('title_fuzzy_threshold', Number(e.target.value))} />
          </label>
        </div>

        <div className="card">
          <h3>{t('settings.pathTemplates')}</h3>
          <label className="field">
            <span>
              {t('settings.albumTemplate')} — {t('settings.templateFields', {
                fields: TEMPLATE_FIELDS,
              })}
            </span>
            <input type="text" className="mono" value={value('path_template') ?? ''}
              onChange={(e) => set('path_template', e.target.value)} />
          </label>
          <label className="field">
            <span>{t('settings.compilationTemplate')}</span>
            <input type="text" className="mono" value={value('compilation_template') ?? ''}
              onChange={(e) => set('compilation_template', e.target.value)} />
          </label>
          <label className="field">
            <span>{t('settings.singleTemplate')}</span>
            <input type="text" className="mono" value={value('single_template') ?? ''}
              onChange={(e) => set('single_template', e.target.value)} />
          </label>
        </div>

        <div className="card">
          <h3>{t('settings.artworkLyrics')}</h3>
          <label className="check">
            <input type="checkbox" checked={!!value('embed_artwork')}
              onChange={(e) => set('embed_artwork', e.target.checked)} />
            {t('settings.embedArtwork')}
          </label>
          <label className="check">
            <input type="checkbox" checked={!!value('write_cover_file')}
              onChange={(e) => set('write_cover_file', e.target.checked)} />
            {t('settings.writeCoverFile')}
          </label>
          <label className="field">
            <span>{t('settings.artworkMinPx')}</span>
            <input type="number" min="0" max="10000"
              value={value('artwork_min_px') ?? 600}
              onChange={(e) => set('artwork_min_px', Number(e.target.value))} />
          </label>
          <label className="check">
            <input type="checkbox" checked={!!value('write_lrc_file')}
              onChange={(e) => set('write_lrc_file', e.target.checked)} />
            {t('settings.writeLrc')}
          </label>
        </div>

        {/* The form has existed since the first release and nothing ever
            rendered it: `ChangePassword` was exported, its endpoint was live,
            its whole translation block was written — and a repo-wide grep for
            the component found exactly one hit, its own definition. So there
            was no way to change your password at all. */}
        <div className="card">
          <h3>{t('auth.changeTitle')}</h3>
          {passwordChanged && <div className="banner">{t('auth.changeDone')}</div>}
          <ChangePassword onDone={() => setPasswordChanged(true)} />
        </div>

        <div className="card">
          <h3>{t('settings.providerKeys')}</h3>
          <p className="muted">{t('settings.secretsHint')}</p>
          {SECRET_FIELDS.map((key) => (
            <label className="field" key={key}>
              <span>
                {key} — {config[key] ? t('settings.configured') : t('settings.notConfigured')}
              </span>
              {/* No `e.target.value &&` guard. It skipped the state update
                  when the box became empty, so `patch[key]` kept whatever was
                  last non-empty — the whole string if it was cleared in one
                  go, a one-character prefix if it was backspaced — and Save
                  then wrote that back. Clearing the box now clears the key. */}
              <input type="text" placeholder="••••••••"
                onChange={(e) => set(key, e.target.value)} />
            </label>
          ))}
          <label className="field">
            <span>{t('settings.musicbrainzContact')}</span>
            <input type="text" value={value('musicbrainz_contact') ?? ''}
              onChange={(e) => set('musicbrainz_contact', e.target.value)} />
          </label>
        </div>

        <div className="card">
          <h3>{t('settings.appleTitle')}</h3>
          <label className="field">
            <span>{t('settings.teamId')}</span>
            <input type="text" value={value('apple_team_id') ?? ''}
              onChange={(e) => set('apple_team_id', e.target.value)} />
          </label>
          <label className="field">
            <span>{t('settings.keyId')}</span>
            <input type="text" value={value('apple_key_id') ?? ''}
              onChange={(e) => set('apple_key_id', e.target.value)} />
          </label>
          {/* Was an editable text field for apple_private_key_path, which the
              policy locks -- so every save answered 400 and discarded the rest
              of the form. And the path it showed was the container's
              /config/AuthKey.p8, which does not exist on the package. Upload
              the key instead: Cadenza puts it in its own data folder, 0600. */}
          <CredentialField kind="apple_private_key"
            label={t('settings.p8File')} hint={t('settings.p8Hint')} />
          <label className="field">
            <span>{t('settings.storefrontHint')}</span>
            <input type="text" value={value('apple_storefront') ?? ''}
              onChange={(e) => set('apple_storefront', e.target.value)} />
          </label>
        </div>
      </div>
    </>
  )
}
