import { useEffect, useState } from 'react'
import { api } from '../api/client'
import { useI18n } from '../i18n'

type Job = {
  id: number; kind: string; state: string; dry_run: boolean
  processed: number; total: number; message: string | null
  created_at: string; finished_at: string | null
}
type Task = {
  id: number; name: string; job_kind: string; cron: string
  params: Record<string, unknown>; enabled: boolean
  last_run: string | null; next_run: string | null
}

const STATE_TAG: Record<string, string> = {
  done: 'ok', failed: 'danger', running: 'blue', pending: '', cancelled: 'warn',
}

export default function Jobs() {
  const { t, d } = useI18n()
  const [jobs, setJobs] = useState<Job[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [kinds, setKinds] = useState<string[]>([])
  const [detail, setDetail] = useState<unknown>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [form, setForm] = useState({ name: '', job_kind: 'scan', cron: '0 3 * * *' })

  const load = () => {
    api.get<{ items: Job[] }>('/jobs?limit=30').then((r) => setJobs(r.items)).catch(() => {})
    api.get<Task[]>('/jobs/schedule/tasks').then(setTasks).catch(() => {})
  }

  useEffect(() => {
    load()
    api.get<string[]>('/jobs/kinds').then(setKinds).catch(() => {})
    const timer = setInterval(load, 8000)
    return () => clearInterval(timer)
  }, [])

  const save = async () => {
    if (!form.name.trim()) { setMessage(t('jobs.nameRequired')); return }
    try {
      await api.post('/jobs/schedule/tasks', { ...form, params: {}, enabled: true })
      setForm({ name: '', job_kind: 'scan', cron: '0 3 * * *' })
      setMessage(t('jobs.saved'))
      load()
    } catch (e) { setMessage((e as Error).message) }
  }

  const toggle = async (task: Task) => {
    await api.post('/jobs/schedule/tasks', { ...task, enabled: !task.enabled })
    load()
  }

  return (
    <>
      <div className="page-head">
        <h1>{t('jobs.title')}</h1>
        <p>{t('jobs.subtitle')}</p>
      </div>
      {message && <div className="banner">{message}</div>}

      <div className="card" style={{ marginBottom: 16 }}>
        <h3>{t('jobs.scheduleTitle')}</h3>
        <div className="toolbar">
          <input type="text" placeholder={t('jobs.taskName')} value={form.name}
            style={{ maxWidth: 240 }}
            onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <select value={form.job_kind} style={{ width: 200 }}
            onChange={(e) => setForm({ ...form, job_kind: e.target.value })}>
            {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
          <input type="text" className="mono" placeholder="0 3 * * *" value={form.cron}
            style={{ maxWidth: 160 }}
            onChange={(e) => setForm({ ...form, cron: e.target.value })} />
          <button className="btn primary" onClick={save}>{t('app.save')}</button>
          <span className="muted">{t('jobs.cronHint')}</span>
        </div>

        <table>
          <thead>
            <tr>
              <th>{t('jobs.colName')}</th>
              <th>{t('jobs.colKind')}</th>
              <th>{t('jobs.colSchedule')}</th>
              <th>{t('jobs.colLastRun')}</th>
              <th>{t('jobs.colNextRun')}</th>
              <th>{t('jobs.colStatus')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id}>
                <td>{task.name}</td>
                <td className="mono">{task.job_kind}</td>
                <td className="mono">{task.cron}</td>
                <td className="muted">{d(task.last_run)}</td>
                <td className="muted">{d(task.next_run)}</td>
                <td>
                  <span className={`tag ${task.enabled ? 'ok' : ''}`}>
                    {task.enabled ? t('jobs.enabled') : t('jobs.disabled')}
                  </span>
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn sm" onClick={() => toggle(task)}>
                    {task.enabled ? t('jobs.disable') : t('jobs.enable')}
                  </button>{' '}
                  <button className="btn sm"
                    onClick={() => api.post(`/jobs/schedule/tasks/${task.id}/run`).then(load)}>
                    {t('jobs.runNow')}
                  </button>{' '}
                  <button className="btn sm danger"
                    onClick={() => api.del(`/jobs/schedule/tasks/${task.id}`).then(load)}>
                    {t('jobs.delete')}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>{t('jobs.historyTitle')}</h3>
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>{t('jobs.colKind')}</th>
              <th>{t('jobs.colStatus')}</th>
              <th>{t('jobs.colProgress')}</th>
              <th>{t('jobs.colMode')}</th>
              <th>{t('jobs.colStarted')}</th>
              <th>{t('jobs.colMessage')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td className="mono">{job.id}</td>
                <td className="mono">{job.kind}</td>
                <td><span className={`tag ${STATE_TAG[job.state] ?? ''}`}>{job.state}</span></td>
                <td>{job.total ? `${job.processed}/${job.total}` : '—'}</td>
                <td>
                  <span className={`tag ${job.dry_run ? '' : 'warn'}`}>
                    {job.dry_run ? t('jobs.modeDryRun') : t('jobs.modeExecute')}
                  </span>
                </td>
                <td className="muted">{d(job.created_at)}</td>
                <td className="muted truncate" style={{ maxWidth: 240 }}>{job.message ?? ''}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn sm"
                    onClick={() => api.get(`/jobs/${job.id}`).then(setDetail)}>
                    {t('jobs.viewResult')}
                  </button>
                  {job.state === 'running' && (
                    <button className="btn sm danger" style={{ marginInlineStart: 4 }}
                      onClick={() => api.post(`/jobs/${job.id}/cancel`).then(load)}>
                      {t('jobs.cancelJob')}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {detail !== null && (
        <div className="card" style={{ marginTop: 16 }}>
          <div style={{ display: 'flex' }}>
            <h3 style={{ margin: 0 }}>{t('jobs.resultTitle')}</h3>
            <div className="spacer" />
            <button className="btn sm" onClick={() => setDetail(null)}>{t('app.close')}</button>
          </div>
          <pre className="mono" style={{ maxHeight: 460, overflow: 'auto', direction: 'ltr' }}>
            {JSON.stringify(detail, null, 2)}
          </pre>
        </div>
      )}
    </>
  )
}
