import { useState } from 'react'
import {
  cancelExportJob, createProjectExport, exportDownloadUrl, getExportJob,
  type ExportFormat, type ExportJob,
} from '../api/client'
import type { ExportLayout, ExportLayoutDiagnostic } from './exportLayout'

interface Props {
  projectId: string
  hasMedia: boolean
  hasTranscript: boolean
  flushPending: () => Promise<void>
  getAssLayout: (onDiagnostic?: (diagnostic: ExportLayoutDiagnostic) => void) => ExportLayout
  getAssDiagnostic?: (
    layout: ExportLayout, diagnosticId: string,
    layoutDiagnostic?: ExportLayoutDiagnostic,
  ) => Record<string, unknown>
}

export function ExportPanel({ projectId, hasMedia, hasTranscript, flushPending, getAssLayout, getAssDiagnostic }: Props) {
  const [format, setFormat] = useState<ExportFormat>('ass')
  const [quality, setQuality] = useState('balanced')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [job, setJob] = useState<ExportJob | null>(null)

  async function exportNow() {
    setBusy(true); setError(''); setMessage(''); setJob(null)
    try {
      await flushPending()
      const diagnosticsEnabled = format === 'mp4'
        && new URLSearchParams(window.location.search).get('exportDiagnostics') === '1'
      const diagnosticId = diagnosticsEnabled ? crypto.randomUUID() : undefined
      let layoutDiagnostic: ExportLayoutDiagnostic | undefined
      const layout = format === 'srt' ? undefined : getAssLayout(
        diagnosticsEnabled ? (value) => { layoutDiagnostic = value } : undefined,
      )
      const diagnostics = diagnosticId && getAssDiagnostic
        ? getAssDiagnostic(layout ?? [], diagnosticId, layoutDiagnostic)
        : undefined
      if (diagnosticId && diagnostics) {
        try { sessionStorage.setItem(`arma-te-export-diagnostic-${diagnosticId}`, JSON.stringify(diagnostics)) }
        catch { /* The backend diagnostic file remains available if storage is disabled. */ }
        console.info('[ARMA-TE export diagnostic]', diagnostics)
      }
      const result = diagnosticId
        ? await createProjectExport(projectId, format, quality, layout, diagnosticId, diagnostics)
        : await createProjectExport(projectId, format, quality, layout)
      if (result.file && result.filename) {
        const url = URL.createObjectURL(result.file)
        const link = document.createElement('a')
        link.href = url; link.download = result.filename; link.click()
        window.setTimeout(() => URL.revokeObjectURL(url), 1000)
        setMessage(`${format.toUpperCase()} exportado: ${result.filename}`)
        return
      }
      if (!result.job) throw new Error('El backend no devolvió el trabajo de exportación.')
      let current = result.job
      setJob(current)
      while (current.state === 'queued' || current.state === 'running') {
        await new Promise((resolve) => window.setTimeout(resolve, 800))
        current = await getExportJob(current.job_id)
        setJob(current)
      }
      if (current.state === 'succeeded') setMessage('Video exportado. Descárgalo cuando quieras.')
      else if (current.state === 'cancelled') setMessage('Exportación cancelada.')
      else setError(current.error?.message ?? 'No se pudo exportar el video.')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo completar la exportación.')
    } finally { setBusy(false) }
  }

  async function cancel() {
    if (!job) return
    try { setJob(await cancelExportJob(job.job_id)); setMessage('Solicitud de cancelación enviada.') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'No se pudo cancelar el trabajo.') }
  }

  const ready = hasTranscript && (format !== 'mp4' || hasMedia)
  const downloadName = job?.download_url?.split('/').pop()
  return <section className="export-panel" aria-label="Exportación">
    <div className="export-heading"><div><p className="eyebrow">SALIDA</p><h3>Exportar</h3></div></div>
    <label>Formato
      <select aria-label="Formato de exportación" value={format} onChange={(event) => setFormat(event.target.value as ExportFormat)} disabled={busy}>
        <option value="srt">SRT</option><option value="ass">ASS con karaoke</option><option value="mp4">Video MP4</option>
      </select>
    </label>
    {format === 'mp4' && <label>Calidad
      <select aria-label="Calidad de video" value={quality} onChange={(event) => setQuality(event.target.value)} disabled={busy}>
        <option value="high">Alta</option><option value="balanced">Equilibrada</option><option value="compact">Compacta</option>
      </select>
    </label>}
    <button type="button" onClick={() => void exportNow()} disabled={busy || !ready}>{busy ? 'Exportando…' : 'Generar archivo'}</button>
    {job && (job.state === 'queued' || job.state === 'running') && <div className="export-job" role="status">
      <span>{job.message ?? (job.state === 'queued' ? 'En espera' : 'Renderizando')}</span>
      {job.progress > 0 && <><progress aria-label="Progreso real de exportación" max="100" value={job.progress} /><span>{job.progress.toFixed(1)}%</span></>}
      <button className="subtle-button" type="button" onClick={() => void cancel()}>Cancelar</button>
    </div>}
    {job?.state === 'succeeded' && job.download_url && downloadName && <a className="export-download" href={exportDownloadUrl(projectId, decodeURIComponent(downloadName))} download>Descargar MP4</a>}
    {message && <p className="export-message" role="status">{message}</p>}
    {error && <p className="export-error" role="alert">{error}</p>}
  </section>
}
