import { LOCALES, useI18n } from '../i18n'

export default function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n()

  return (
    <div className="lang-switch" role="group" aria-label={t('app.language')}>
      {LOCALES.map((l) => (
        <button
          key={l.code}
          type="button"
          className={`lang-btn${l.code === locale ? ' active' : ''}`}
          aria-pressed={l.code === locale}
          onClick={() => setLocale(l.code)}
        >
          {l.label}
        </button>
      ))}
    </div>
  )
}
