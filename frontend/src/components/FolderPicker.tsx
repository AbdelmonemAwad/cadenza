import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n } from '../i18n'

type Entry = { name: string; path: string; readable: boolean }
type FileEntry = { name: string; path: string; size: number }
type Listing = {
  path: string; parent: string | null
  entries: Entry[]; files: FileEntry[]; truncated: boolean
}
type Root = { path: string; name: string }

/**
 * Browse to a folder instead of typing its path.
 *
 * On a NAS the alternative is: open File Station, find the folder, read the
 * path off the title bar, and retype it here — where a typo produces a folder
 * that silently does not exist.
 *
 * Folders the service account cannot enter are shown and disabled rather than
 * hidden. That state is the normal one for a share Cadenza has not been
 * granted, and it is the single thing the user has to go and fix, so hiding it
 * would remove the only clue.
 */
export default function FolderPicker({ value, credential, onPick, onClose }: {
  value?: string
  /** When set, files with that credential's extensions are listed too and can
   *  be chosen directly. Without it this is a folder picker and the server
   *  returns no file names at all. */
  credential?: string
  onPick: (path: string) => void
  onClose: () => void
}) {
  const { t } = useI18n()
  const [roots, setRoots] = useState<Root[]>([])
  const [listing, setListing] = useState<Listing | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const open = (path: string) => {
    setBusy(true)
    setError(null)
    const q = new URLSearchParams({ path })
    if (credential) q.set('credential', credential)
    api.get<Listing>(`/files/browse?${q}`)
      .then(setListing)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false))
  }

  useEffect(() => {
    api.get<{ roots: Root[] }>('/files/roots')
      .then((r) => {
        setRoots(r.roots)
        const start = value && value.startsWith('/') ? value : r.roots[0]?.path
        if (start) open(start)
      })
      .catch((e) => setError((e as Error).message))
    // Runs once: reopening on every keystroke in the field behind it would
    // reset the user's position in the tree.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="card" style={{ marginTop: 8 }}>
      <div className="toolbar" style={{ marginBottom: 8 }}>
        <strong>{t('files.pickFolder')}</strong>
        <div className="spacer" />
        <button className="btn sm" onClick={onClose}>{t('app.close')}</button>
      </div>

      <div className="toolbar" style={{ marginBottom: 8, flexWrap: 'wrap' }}>
        {roots.map((r) => (
          <button key={r.path} className="btn sm" onClick={() => open(r.path)}>
            {r.name || r.path}
          </button>
        ))}
      </div>

      {error && <div className="banner">{error}</div>}

      {listing && (
        <>
          <p className="mono muted" style={{ margin: '4px 0', direction: 'ltr' }}>
            {listing.path}
          </p>
          <div style={{ maxHeight: 260, overflowY: 'auto', margin: '8px 0' }}>
            {listing.parent && (
              <button className="btn sm" style={{ display: 'block', width: '100%',
                textAlign: 'start', marginBottom: 2 }}
                onClick={() => open(listing.parent!)}>
                ../
              </button>
            )}
            {listing.entries.map((e) => (
              <button key={e.path} className="btn sm"
                disabled={!e.readable}
                title={e.readable ? e.path : t('files.notReadable')}
                style={{ display: 'block', width: '100%', textAlign: 'start',
                  marginBottom: 2, opacity: e.readable ? 1 : 0.5 }}
                onClick={() => open(e.path)}>
                {e.name}{e.readable ? '' : `  — ${t('files.notReadable')}`}
              </button>
            ))}
            {(listing.files ?? []).map((f) => (
              <button key={f.path} className="btn sm primary"
                style={{ display: 'block', width: '100%', textAlign: 'start',
                  marginBottom: 2 }}
                onClick={() => { onPick(f.path); onClose() }}>
                {f.name}
              </button>
            ))}
            {!listing.entries.length && !(listing.files ?? []).length && !busy && (
              <p className="muted">{t('files.noSubfolders')}</p>
            )}
          </div>
          {listing.truncated && <p className="muted">{t('files.truncated')}</p>}
          <div className="toolbar" style={{ marginBottom: 0 }}>
            {!credential && (
              <button className="btn primary sm"
                onClick={() => { onPick(listing.path); onClose() }}>
                {t('files.useThisFolder')}
              </button>
            )}
            <span className="muted mono truncate" style={{ direction: 'ltr' }}>
              {listing.path}
            </span>
          </div>
        </>
      )}
    </div>
  )
}
