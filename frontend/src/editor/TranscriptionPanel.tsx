import { useEffect, useRef, useState } from 'react'
import {
  getTranscriptionJob,
  openProject,
  startTranscriptionJob,
  type ProjectDocument,
  type TranscriptionJob,
  type TranscriptionModel,
} from '../api/client'

interface Props {
  projectId: string
  hasAudioSource: boolean
  hasTranscript: boolean
  flushPending: () => Promise<void>
  onProjectUpdated: (project: ProjectDocument) => void
}

const POLL_INTERVAL_MS = 800

function storageKey(projectId: string): string {
  return `arma-te-transcription-job:${projectId}`
}

function readStoredJob(projectId: string): string | null {
  try { return window.sessionStorage.getItem(storageKey(projectId)) } catch { return null }
}

function clearStoredJob(projectId: string, jobId: string): void {
  try {
    if (window.sessionStorage.getItem(storageKey(projectId)) === jobId) {
      window.sessionStorage.removeItem(storageKey(projectId))
    }
  } catch { /* Job status remains available in component state. */ }
}

function isActive(state: TranscriptionJob['state'] | undefined): boolean {
  return state === 'queued' || state === 'running'
}

export function TranscriptionPanel({ projectId, hasAudioSource, hasTranscript, flushPending, onProjectUpdated }: Props) {
  const [model, setModel] = useState<TranscriptionModel>('small')
  const [language, setLanguage] = useState<'es' | null>('es')
  const [jobId, setJobId] = useState<string | null>(() => readStoredJob(projectId))
  const [job, setJob] = useState<TranscriptionJob | null>(null)
  const [starting, setStarting] = useState(false)
  const [recovering, setRecovering] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [pollRevision, setPollRevision] = useState(0)
  const startLockRef = useRef(false)
  const mountedRef = useRef(true)
  const projectIdRef = useRef(projectId)
  const onProjectUpdatedRef = useRef(onProjectUpdated)
  projectIdRef.current = projectId
  onProjectUpdatedRef.current = onProjectUpdated

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  async function recoverProject(expectedProjectId: string, completedJobId: string): Promise<void> {
    setRecovering(true)
    setError('')
    try {
      const updated = await openProject(expectedProjectId)
      if (!mountedRef.current || projectIdRef.current !== expectedProjectId) return
      clearStoredJob(expectedProjectId, completedJobId)
      setJobId(null)
      setMessage('Transcripción generada y proyecto actualizado.')
      onProjectUpdatedRef.current(updated)
    } catch (cause) {
      if (mountedRef.current && projectIdRef.current === expectedProjectId) {
        setError(cause instanceof Error
          ? `La transcripción terminó, pero no se pudo actualizar el proyecto: ${cause.message}`
          : 'La transcripción terminó, pero no se pudo actualizar el proyecto.')
      }
    } finally {
      if (mountedRef.current && projectIdRef.current === expectedProjectId) setRecovering(false)
    }
  }

  useEffect(() => {
    if (!jobId) return
    const activeJobId = jobId
    let active = true
    const controller = new AbortController()
    const expectedProjectId = projectId
    let timer = window.setTimeout(() => { void poll() }, POLL_INTERVAL_MS)

    async function poll(): Promise<void> {
      try {
        const current = await getTranscriptionJob(activeJobId, controller.signal)
        if (!active || projectIdRef.current !== expectedProjectId) return
        setJob(current)
        setError('')
        if (isActive(current.state)) {
          timer = window.setTimeout(() => { void poll() }, POLL_INTERVAL_MS)
          return
        }
        if (current.state === 'succeeded') {
          await recoverProject(expectedProjectId, activeJobId)
        } else {
          clearStoredJob(expectedProjectId, activeJobId)
          setJobId(null)
          if (current.state === 'failed') setError(current.error?.message ?? 'No se pudo generar la transcripción.')
        }
      } catch (cause) {
        if (!active || controller.signal.aborted || projectIdRef.current !== expectedProjectId) return
        setError(cause instanceof Error ? cause.message : 'No se pudo consultar el estado de la transcripción.')
        timer = window.setTimeout(() => { void poll() }, POLL_INTERVAL_MS)
      }
    }

    return () => {
      active = false
      window.clearTimeout(timer)
      controller.abort()
    }
  // A retry intentionally starts a fresh poll for the same stored job.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, pollRevision, projectId])

  async function start(): Promise<void> {
    if (startLockRef.current || starting || jobId || !hasAudioSource || hasTranscript) return
    startLockRef.current = true
    setStarting(true)
    setError('')
    setMessage('')
    const requestedProjectId = projectId
    try {
      await flushPending()
      if (!mountedRef.current || projectIdRef.current !== requestedProjectId) return
      const created = await startTranscriptionJob(requestedProjectId, { model_size: model, language })
      try { window.sessionStorage.setItem(storageKey(requestedProjectId), created.job_id) } catch { /* In-memory tracking still works. */ }
      if (!mountedRef.current || projectIdRef.current !== requestedProjectId) return
      setJob(created)
      setJobId(created.job_id)
      setMessage(created.message ?? 'Trabajo de transcripción iniciado.')
    } catch (cause) {
      if (mountedRef.current && projectIdRef.current === requestedProjectId) {
        setError(cause instanceof Error ? cause.message : 'No se pudo iniciar la transcripción.')
      }
    } finally {
      startLockRef.current = false
      if (mountedRef.current && projectIdRef.current === requestedProjectId) setStarting(false)
    }
  }

  const active = starting || isActive(job?.state) || Boolean(jobId && !job)
  const canStart = hasAudioSource && !hasTranscript && !active && !jobId && !recovering

  return <section className="export-panel transcription-panel" aria-label="Transcripción">
    <div className="export-heading"><div><p className="eyebrow">AUTOMÁTICA</p><h3>Transcripción</h3></div></div>
    <label>Modelo
      <select aria-label="Modelo de transcripción" value={model} onChange={(event) => setModel(event.target.value as TranscriptionModel)} disabled={active || hasTranscript}>
        <option value="tiny">Tiny</option><option value="base">Base</option><option value="small">Small</option><option value="medium">Medium</option>
      </select>
    </label>
    <label>Idioma
      <select aria-label="Idioma de transcripción" value={language ?? 'auto'} onChange={(event) => setLanguage(event.target.value === 'auto' ? null : 'es')} disabled={active || hasTranscript}>
        <option value="es">Español</option><option value="auto">Automático</option>
      </select>
    </label>
    <button type="button" onClick={() => void start()} disabled={!canStart}>
      {starting ? 'Iniciando…' : hasTranscript ? 'Transcripción disponible' : 'Generar transcripción'}
    </button>
    {job && <div className="export-job" role="status" aria-live="polite">
      <span>Estado: {job.state}</span>
      {job.message && <span>{job.message}</span>}
      {job.state === 'running' && <progress aria-label="Transcripción en curso" />}
    </div>}
    {jobId && job?.state === 'succeeded' && <button className="subtle-button" type="button" onClick={() => void recoverProject(projectId, jobId)} disabled={recovering}>
      {recovering ? 'Actualizando proyecto…' : 'Recuperar proyecto'}
    </button>}
    {jobId && error && job?.state !== 'succeeded' && <button className="subtle-button" type="button" onClick={() => { setError(''); setPollRevision((value) => value + 1) }}>
      Reintentar consulta
    </button>}
    {message && <p className="export-message" role="status">{message}</p>}
    {error && <p className="export-error" role="alert">{error}</p>}
    {!hasAudioSource && <p className="muted">Importa un WAV, MP3 o MP4 con audio para generar la transcripción.</p>}
  </section>
}
