import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from 'react'
import ar from './ar'
import en from './en'

export type Locale = 'en' | 'ar'

export const LOCALES: { code: Locale; label: string; dir: 'ltr' | 'rtl' }[] = [
  { code: 'en', label: 'English', dir: 'ltr' },
  { code: 'ar', label: 'العربية', dir: 'rtl' },
]

const CATALOGUES = { en, ar } as const
const STORAGE_KEY = 'cadenza.locale'

/** Dotted paths into the catalogue, e.g. "nav.dashboard". */
type Leaves<T, Prefix extends string = ''> = {
  [K in keyof T & string]: T[K] extends string
    ? `${Prefix}${K}`
    : Leaves<T[K], `${Prefix}${K}.`>
}[keyof T & string]

export type TranslationKey = Leaves<typeof en>
export type Vars = Record<string, string | number>

function resolve(catalogue: unknown, key: string): string | undefined {
  const value = key.split('.').reduce<unknown>(
    (acc, part) => (acc && typeof acc === 'object' ? (acc as never)[part] : undefined),
    catalogue,
  )
  return typeof value === 'string' ? value : undefined
}

/** Replaces {name} placeholders. Unknown placeholders are left untouched. */
function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template
  return template.replace(/\{(\w+)\}/g, (match, name) =>
    name in vars ? String(vars[name]) : match)
}

function detectInitialLocale(): Locale {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'en' || stored === 'ar') return stored
  return navigator.language?.toLowerCase().startsWith('ar') ? 'ar' : 'en'
}

type I18nValue = {
  locale: Locale
  dir: 'ltr' | 'rtl'
  setLocale: (l: Locale) => void
  t: (key: TranslationKey, vars?: Vars) => string
  /** Locale-aware number formatting for counts and sizes. */
  n: (value: number) => string
  d: (value: string | number | Date | null | undefined) => string
}

const I18nContext = createContext<I18nValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectInitialLocale)
  const dir = locale === 'ar' ? 'rtl' : 'ltr'

  useEffect(() => {
    // The whole DSM-style layout relies on logical CSS properties, so flipping
    // `dir` on the root element is all that is needed to mirror the UI.
    document.documentElement.lang = locale
    document.documentElement.dir = dir
    localStorage.setItem(STORAGE_KEY, locale)
  }, [locale, dir])

  const setLocale = useCallback((l: Locale) => setLocaleState(l), [])

  const t = useCallback((key: TranslationKey, vars?: Vars): string => {
    const catalogue = CATALOGUES[locale]
    // Fall back to English so a missing translation degrades instead of
    // rendering a raw key at the user.
    const template = resolve(catalogue, key) ?? resolve(en, key) ?? key
    return interpolate(template, vars)
  }, [locale])

  const n = useCallback(
    (value: number) => new Intl.NumberFormat(locale).format(value), [locale])

  /**
   * Format a timestamp in the viewer's own timezone.
   *
   * The `Z` is load-bearing. Cadenza stores UTC and serialises it without an
   * offset — `"2026-07-27T09:12:33.123456"` — and ECMA-262 says a date-time
   * string with no offset is *local* time. So every timestamp in the app was
   * read as if the NAS clock were the browser's clock and then displayed as
   * local, shifting it by the viewer's UTC offset: on a UTC+4 machine every
   * "Started", "Moved at", "Purge after" and audit-log entry read four hours
   * late. It looked plausible, which is why nobody caught it.
   *
   * A string that already carries an offset (or a `Z`) is left alone, so this
   * keeps working if the backend starts sending them.
   */
  const d = useCallback((value: string | number | Date | null | undefined) => {
    if (value === null || value === undefined) return '—'
    let date: Date
    if (value instanceof Date) {
      date = value
    } else if (typeof value === 'string' && !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) {
      date = new Date(`${value}Z`)
    } else {
      date = new Date(value)
    }
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString(locale)
  }, [locale])

  const value = useMemo<I18nValue>(
    () => ({ locale, dir, setLocale, t, n, d }), [locale, dir, setLocale, t, n, d])

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext)
  if (!ctx) throw new Error('useI18n must be used inside <I18nProvider>')
  return ctx
}
