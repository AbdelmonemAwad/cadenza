import React from 'react'
import ReactDOM from 'react-dom/client'
import { HashRouter } from 'react-router-dom'
import App from './App'
import { I18nProvider } from './i18n'
import './theme/dsm.css'

// HashRouter rather than BrowserRouter: it works unchanged behind the DSM
// reverse proxy no matter what base path the package is mounted under.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <HashRouter>
        <App />
      </HashRouter>
    </I18nProvider>
  </React.StrictMode>,
)
