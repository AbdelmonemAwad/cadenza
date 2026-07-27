import { useEffect, useState } from 'react'
import { api } from '../api/client'
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

export default function Settings() {
  const { t } = useI18n()
  const [config, setConfig] = useState<Record<string, any> | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [patch, setPatch] = useState<Record<string, any>>({})
  const [message, setMessage] = useState<string | null>(null)

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
            <input type="number" value={value('quarantine_retention_days') ?? 30}
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
            <input type="number" step="0.01" min="0.5" max="0.999"
              value={value('acoustic_match_threshold') ?? 0.9}
              onChange={(e) => set('acoustic_match_threshold', Number(e.target.value))} />
          </label>
          <p className="muted" style={{ fontSize: 12, marginTop: -6 }}>
            {t('settings.acousticThresholdHint')}
          </p>
          <label className="field">
            <span>{t('settings.durationTolerance')}</span>
            <input type="number" value={value('duration_tolerance_s') ?? 7}
              onChange={(e) => set('duration_tolerance_s', Number(e.target.value))} />
          </label>
          <label className="field">
            <span>{t('settings.fuzzyThreshold')}</span>
            <input type="number" value={value('title_fuzzy_threshold') ?? 88}
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
            <input type="number" value={value('artwork_min_px') ?? 600}
              onChange={(e) => set('artwork_min_px', Number(e.target.value))} />
          </label>
          <label className="check">
            <input type="checkbox" checked={!!value('write_lrc_file')}
              onChange={(e) => set('write_lrc_file', e.target.checked)} />
            {t('settings.writeLrc')}
          </label>
        </div>

        <div className="card">
          <h3>{t('settings.providerKeys')}</h3>
          <p className="muted">{t('settings.secretsHint')}</p>
          {SECRET_FIELDS.map((key) => (
            <label className="field" key={key}>
              <span>
                {key} — {config[key] ? t('settings.configured') : t('settings.notConfigured')}
              </span>
              <input type="text" placeholder="••••••••"
                onChange={(e) => e.target.value && set(key, e.target.value)} />
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
          <label className="field">
            <span>{t('settings.p8Path')}</span>
            <input type="text" className="mono" value={value('apple_private_key_path') ?? ''}
              onChange={(e) => set('apple_private_key_path', e.target.value)} />
          </label>
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
