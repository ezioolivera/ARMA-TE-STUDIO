import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectDocument } from '../api/client'
import { TranscriptionPanel } from './TranscriptionPanel'

const api = vi.hoisted(() => ({ start: vi.fn(), get: vi.fn(), open: vi.fn() }))
vi.mock('../api/client', () => ({
  startTranscriptionJob: api.start,
  getTranscriptionJob: api.get,
  openProject: api.open,
}))

function job(state: 'queued' | 'running' | 'succeeded' | 'failed', id = 'job-1') {
  return { job_id: id, state, progress: state === 'succeeded' ? 100 : 0, message: `Mensaje ${state}`, cancel_requested: false,
    error: state === 'failed' ? { code: 'transcription_failed', message: 'Falló la inferencia', retryable: true } : null }
}

const loadedProject: ProjectDocument = {
  id: 'project-1', name: 'Proyecto', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  metadata: {}, document: { transcription: { segments: [] } }, media: null,
  transcription: null, transcription_info: null,
}
const flush = vi.fn().mockResolvedValue(undefined)

function renderPanel(projectId = 'project-1', onProjectUpdated = vi.fn()) {
  return render(<TranscriptionPanel
    projectId={projectId}
    hasMediaAudio
    hasTranscript={false}
    flushPending={flush}
    onProjectUpdated={onProjectUpdated}
  />)
}

describe('transcription panel', () => {
  beforeEach(() => {
    vi.useRealTimers()
    sessionStorage.clear()
    flush.mockClear().mockResolvedValue(undefined)
    api.start.mockReset()
    api.get.mockReset()
    api.open.mockReset()
  })

  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('sends the selected model and language, polls every 800 ms, then reloads the project', async () => {
    const updated = vi.fn()
    api.start.mockResolvedValue(job('queued'))
    api.get.mockResolvedValue(job('succeeded'))
    api.open.mockResolvedValue(loadedProject)
    renderPanel('project-1', updated)

    fireEvent.change(screen.getByLabelText('Modelo de transcripción'), { target: { value: 'medium' } })
    fireEvent.change(screen.getByLabelText('Idioma de transcripción'), { target: { value: 'auto' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generar transcripción' }))
    await waitFor(() => expect(api.start).toHaveBeenCalledOnce())

    expect(flush).toHaveBeenCalledOnce()
    expect(api.start).toHaveBeenCalledWith('project-1', { model_size: 'medium', language: null })
    expect(sessionStorage.getItem('arma-te-transcription-job:project-1')).toBe('job-1')
    expect((screen.getByRole('button', { name: 'Generar transcripción' }) as HTMLButtonElement).disabled).toBe(true)

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('job-1', expect.any(AbortSignal)))
    expect(api.open).toHaveBeenCalledWith('project-1')
    expect(updated).toHaveBeenCalledWith(loadedProject)
    expect(sessionStorage.getItem('arma-te-transcription-job:project-1')).toBeNull()
    expect(screen.getByText('Transcripción generada y proyecto actualizado.')).toBeTruthy()
  })

  it('shows backend errors and does not display a guessed percentage', async () => {
    api.start.mockResolvedValue(job('queued', 'job-failed'))
    api.get.mockResolvedValue(job('failed', 'job-failed'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Generar transcripción' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledOnce())

    expect((await screen.findByRole('alert')).textContent).toContain('Falló la inferencia')
    expect(screen.getByText('Estado: failed')).toBeTruthy()
    expect(screen.queryByText(/\d+%/)).toBeNull()
    expect(sessionStorage.getItem('arma-te-transcription-job:project-1')).toBeNull()
  })

  it('shows a readable error when the backend rejects the start request', async () => {
    api.start.mockRejectedValue(new Error('El video no contiene audio.'))
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Generar transcripción' }))
    expect((await screen.findByRole('alert')).textContent).toContain('El video no contiene audio.')
    expect(api.get).not.toHaveBeenCalled()
    expect(sessionStorage.getItem('arma-te-transcription-job:project-1')).toBeNull()
  })

  it('prevents duplicate starts while the first submission is pending', async () => {
    let finishFlush!: () => void
    flush.mockImplementationOnce(() => new Promise<void>((resolve) => { finishFlush = resolve }))
    api.start.mockResolvedValue(job('queued'))
    renderPanel()
    const button = screen.getByRole('button', { name: 'Generar transcripción' })
    fireEvent.click(button)
    fireEvent.click(button)
    expect(flush).toHaveBeenCalledOnce()
    expect(api.start).not.toHaveBeenCalled()
    await act(async () => { finishFlush(); await Promise.resolve(); await Promise.resolve() })
    expect(api.start).toHaveBeenCalledOnce()
  })

  it('allows project recovery after a successful job when the first project fetch fails', async () => {
    api.start.mockResolvedValue(job('queued'))
    api.get.mockResolvedValue(job('succeeded'))
    api.open.mockRejectedValueOnce(new Error('Conexión interrumpida')).mockResolvedValueOnce(loadedProject)
    const updated = vi.fn()
    renderPanel('project-1', updated)
    fireEvent.click(screen.getByRole('button', { name: 'Generar transcripción' }))
    await waitFor(() => expect(api.get).toHaveBeenCalledOnce())

    expect((await screen.findByRole('alert')).textContent).toContain('no se pudo actualizar el proyecto')
    expect(screen.getByRole('button', { name: 'Recuperar proyecto' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Recuperar proyecto' }))
    await waitFor(() => expect(updated).toHaveBeenCalledWith(loadedProject))
    expect(api.start).toHaveBeenCalledOnce()
  })

  it('ignores a late job response after switching projects', async () => {
    sessionStorage.setItem('arma-te-transcription-job:project-1', 'old-job')
    let resolveStatus!: (value: ReturnType<typeof job>) => void
    api.get.mockImplementation(() => new Promise((resolve) => { resolveStatus = resolve }))
    const updated = vi.fn()
    const view = renderPanel('project-1', updated)
    await waitFor(() => expect(api.get).toHaveBeenCalledOnce())
    view.rerender(<TranscriptionPanel projectId="project-2" hasMediaAudio hasTranscript={false} flushPending={flush} onProjectUpdated={updated} />)
    await act(async () => { resolveStatus(job('succeeded', 'old-job')); await Promise.resolve() })
    expect(api.open).not.toHaveBeenCalled()
    expect(updated).not.toHaveBeenCalled()
  })
})
