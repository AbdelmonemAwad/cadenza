const BASE = '/api/v1'

/** Fired when the API rejects a request for lack of a session, so the app can
    drop straight back to the sign-in screen instead of rendering empty pages. */
export const UNAUTHORIZED_EVENT = 'cadenza:unauthorized'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // A FormData body must carry no Content-Type from here: the browser sets it
  // itself so that it can append the multipart boundary, and a hand-written
  // header produces a body the server cannot parse. Merging an empty object
  // over the default does not remove it, so the default is skipped outright.
  const isForm = init?.body instanceof FormData
  const headers: HeadersInit = isForm
    ? { ...(init?.headers || {}) }
    : { 'Content-Type': 'application/json', ...(init?.headers || {}) }

  const res = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',   // carry the session cookie
    ...init,
    headers,
  })
  if (!res.ok) {
    // Do not fire on the auth endpoints themselves: a failed sign-in is a
    // normal outcome there and would otherwise loop.
    if (res.status === 401 && !path.startsWith('/auth/')) {
      window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT))
    }
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch { /* body was not JSON; keep the status text */ }
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PATCH', body: JSON.stringify(body) }),
  put: <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PUT', body: JSON.stringify(body) }),
  del: <T>(p: string) => request<T>(p, { method: 'DELETE' }),

  /** Upload a file as multipart/form-data. `request` recognises the FormData
   *  body and leaves the Content-Type to the browser. */
  upload: <T>(p: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<T>(p, { method: 'POST', body: form })
  },
}

export type JobEvent = {
  type: 'job.queued' | 'job.started' | 'job.progress' | 'job.finished' | 'ping'
  job_id?: number
  kind?: string
  processed?: number
  total?: number
  message?: string | null
  state?: string
  result?: Record<string, unknown>
}

/** Subscribes to the job progress feed, reconnecting with backoff. */
export function subscribeJobs(onEvent: (e: JobEvent) => void): () => void {
  let ws: WebSocket | null = null
  let closed = false
  let retry = 1000

  const connect = () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    ws = new WebSocket(`${proto}//${location.host}${BASE}/jobs/stream`)
    ws.onopen = () => { retry = 1000 }
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data) as JobEvent
        if (data.type !== 'ping') onEvent(data)
      } catch { /* malformed frame; ignore it rather than kill the socket */ }
    }
    ws.onclose = () => {
      if (closed) return
      setTimeout(connect, retry)
      retry = Math.min(retry * 2, 30000)
    }
    ws.onerror = () => ws?.close()
  }
  connect()

  return () => { closed = true; ws?.close() }
}

export function humanBytes(n: number | null | undefined): string {
  if (!n) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n, i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function humanDuration(s: number | null | undefined): string {
  if (!s) return '—'
  const m = Math.floor(s / 60)
  return `${m}:${String(Math.floor(s % 60)).padStart(2, '0')}`
}
