export interface HealthResponse {
  status: 'ok'
  application: string
  version: string
}

export interface MediaInfo {
  filename: string
  duration: number | null
  width: number | null
  height: number | null
  fps: number | null
  video_codec: string | null
  audio_codec: string | null
  has_audio: boolean
  probe_error: string | null
}

export interface TranscriptInfo {
  filename: string
  language: string | null
  segment_count: number
  word_count: number
}

export interface ProjectSummary {
  id: string
  name: string
  updated_at: string
  media: MediaInfo | null
  transcription_info: TranscriptInfo | null
}

export interface ProjectDocument extends ProjectSummary {
  created_at: string
  metadata: Record<string, unknown>
  document: Record<string, unknown>
  transcription: Record<string, unknown> | null
}

export type ExportFormat = 'srt' | 'ass' | 'mp4'
export type TranscriptionModel = 'tiny' | 'base' | 'small' | 'medium'
export type TranscriptionJobState = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
export interface TranscriptionJob {
  job_id: string
  state: TranscriptionJobState
  progress: number
  message: string | null
  cancel_requested: boolean
  error: { code: string; message: string; retryable: boolean } | null
}
import type { ExportLayout } from '../editor/exportLayout'
export type ExportJob = {
  job_id: string
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  message: string | null
  cancel_requested: boolean
  error: { code: string; message: string; retryable: boolean } | null
  download_url: string | null
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '/api/v1').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit, includeBackendDetail = false): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${apiBaseUrl}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...init?.headers,
      },
    })
  } catch {
    if (init?.signal?.aborted) throw new DOMException('Request aborted', 'AbortError')
    throw new ApiError('No se pudo conectar con el backend local.', 0)
  }

  if (!response.ok) {
    if (includeBackendDetail) {
      try {
        const body = await response.json() as { detail?: unknown }
        if (typeof body.detail === 'string' && body.detail.trim()) {
          throw new ApiError(body.detail, response.status)
        }
      } catch (cause) {
        if (cause instanceof ApiError) throw cause
      }
    }
    throw new ApiError(`El backend respondió con HTTP ${response.status}.`, response.status)
  }
  return (await response.json()) as T
}

export function startTranscriptionJob(
  projectId: string,
  options: { model_size: TranscriptionModel; language: 'es' | null },
): Promise<TranscriptionJob> {
  return request<TranscriptionJob>(`/projects/${encodeURIComponent(projectId)}/transcription-jobs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(options),
  }, true)
}

export function getTranscriptionJob(jobId: string, signal?: AbortSignal): Promise<TranscriptionJob> {
  return request<TranscriptionJob>(`/transcription-jobs/${encodeURIComponent(jobId)}`, { signal }, true)
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health')
}

export function listProjects(): Promise<ProjectSummary[]> {
  return request<ProjectSummary[]>('/projects')
}

export function createProject(name: string): Promise<ProjectSummary> {
  return request<ProjectSummary>('/projects', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
}

export function openProject(projectId: string): Promise<ProjectDocument> {
  return request<ProjectDocument>(`/projects/${encodeURIComponent(projectId)}`)
}

export function updateProjectDocument(
  projectId: string,
  document: Record<string, unknown>,
): Promise<ProjectDocument> {
  return request<ProjectDocument>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document }),
  })
}

export function deleteProject(projectId: string): Promise<{ id: string; deleted: boolean }> {
  return request<{ id: string; deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'DELETE',
  })
}

export async function importProjectFile<T>(
  projectId: string,
  kind: 'media' | 'transcription',
  file: File,
): Promise<T> {
  const query = new URLSearchParams({ filename: file.name })
  let response: Response
  try {
    response = await fetch(
      `${apiBaseUrl}/projects/${encodeURIComponent(projectId)}/${kind}?${query}`,
      { method: 'POST', headers: { 'Content-Type': file.type || 'application/octet-stream' }, body: file },
    )
  } catch {
    throw new ApiError('No se pudo conectar con el backend local.', 0)
  }
  if (!response.ok) {
    let detail = `El backend respondió con HTTP ${response.status}.`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch { /* Keep the HTTP status message. */ }
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export function mediaUrl(projectId: string): string {
  return `${apiBaseUrl}/projects/${encodeURIComponent(projectId)}/media`
}

export async function createProjectExport(
  projectId: string, format: ExportFormat, quality = 'balanced', layout?: ExportLayout,
  diagnosticId?: string, diagnostics?: Record<string, unknown>,
): Promise<{ file?: Blob; filename?: string; job?: ExportJob }> {
  const headers: Record<string, string> = {
    Accept: format === 'mp4' ? 'application/json' : 'application/octet-stream',
    'Content-Type': 'application/json',
  }
  if (diagnosticId) headers['X-Export-Diagnostic-ID'] = diagnosticId
  const response = await fetch(`${apiBaseUrl}/projects/${encodeURIComponent(projectId)}/exports`, {
    method: 'POST', headers,
    body: JSON.stringify({ format, quality, ...(layout ? { layout } : {}), ...(diagnostics ? { diagnostics } : {}) }),
  })
  if (!response.ok) {
    let detail = `Error de exportación HTTP ${response.status}.`
    try { const body = await response.json() as { detail?: string }; if (body.detail) detail = body.detail } catch { /* keep status detail */ }
    throw new ApiError(detail, response.status)
  }
  if (format === 'mp4') return { job: await response.json() as ExportJob }
  const disposition = response.headers.get('content-disposition') ?? ''
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : plain ?? `subtitles.${format}`
  return { file: await response.blob(), filename }
}

export async function getExportJob(jobId: string): Promise<ExportJob> {
  return request<ExportJob>(`/jobs/${encodeURIComponent(jobId)}`)
}

export async function cancelExportJob(jobId: string): Promise<ExportJob> {
  return request<ExportJob>(`/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' })
}

export function exportDownloadUrl(projectId: string, filename: string): string {
  return `${apiBaseUrl}/projects/${encodeURIComponent(projectId)}/exports/${encodeURIComponent(filename)}`
}
