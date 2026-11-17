import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExportPanel } from './ExportPanel'

const api = vi.hoisted(() => ({ create: vi.fn(), get: vi.fn(), cancel: vi.fn() }))
vi.mock('../api/client', () => ({
  createProjectExport: api.create,
  getExportJob: api.get,
  cancelExportJob: api.cancel,
  exportDownloadUrl: (_project: string, file: string) => `/api/v1/projects/project/exports/${file}`,
}))

describe('export panel', () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks() })

  it('flushes before generating and downloads SRT and ASS blobs', async () => {
    const flush = vi.fn().mockResolvedValue(undefined)
    const blob = new Blob(['subtitle'])
    const createObjectURL = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:subtitle')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined)
    api.create.mockResolvedValue({ file: blob, filename: 'clip.srt' })
    const getAssLayout = vi.fn().mockReturnValue([])
    render(<ExportPanel projectId="project" hasMedia hasTranscript flushPending={flush} getAssLayout={getAssLayout} />)
    const linkClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    fireEvent.click(screen.getByRole('button', { name: 'Generar archivo' }))
    await waitFor(() => expect(api.create).toHaveBeenCalledWith('project', 'ass', 'balanced', []))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('ASS exportado'))
    expect(flush).toHaveBeenCalledOnce()
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(linkClick).toHaveBeenCalledOnce()
    fireEvent.change(screen.getByLabelText('Formato de exportación'), { target: { value: 'srt' } })
    api.create.mockResolvedValue({ file: blob, filename: 'clip.srt' })
    fireEvent.click(screen.getByRole('button', { name: 'Generar archivo' }))
    await waitFor(() => expect(api.create).toHaveBeenLastCalledWith('project', 'srt', 'balanced', undefined))
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('SRT exportado'))
    expect(flush).toHaveBeenCalledTimes(2)
    expect(linkClick).toHaveBeenCalledTimes(2)
  })

  it('polls actual MP4 job progress, reports completion and exposes its download', async () => {
    api.create.mockResolvedValue({ job: { job_id: 'job-1', state: 'queued', progress: 0, message: 'En cola', cancel_requested: false, error: null, download_url: null } })
    api.get
      .mockResolvedValueOnce({ job_id: 'job-1', state: 'running', progress: 43.5, message: 'Renderizando 43.5%', cancel_requested: false, error: null, download_url: null })
      .mockResolvedValueOnce({ job_id: 'job-1', state: 'succeeded', progress: 100, message: 'Completado', cancel_requested: false, error: null, download_url: '/api/v1/projects/project/exports/clip.mp4' })
    render(<ExportPanel projectId="project" hasMedia hasTranscript flushPending={vi.fn().mockResolvedValue(undefined)} getAssLayout={() => []} />)
    fireEvent.change(screen.getByLabelText('Formato de exportación'), { target: { value: 'mp4' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generar archivo' }))
    await waitFor(() => expect(screen.getByLabelText('Progreso real de exportación')).toBeTruthy())
    expect(api.get).toHaveBeenCalledWith('job-1')
    expect((await screen.findByRole('link', { name: 'Descargar MP4' })).getAttribute('href')).toBe('/api/v1/projects/project/exports/clip.mp4')
    expect(screen.getByRole('status').textContent).toContain('Video exportado')
  })

  it('surfaces job failures and does not claim a guessed percentage', async () => {
    api.create.mockResolvedValue({ job: { job_id: 'job-error', state: 'queued', progress: 0, message: null, cancel_requested: false, error: null, download_url: null } })
    api.get.mockResolvedValue({ job_id: 'job-error', state: 'failed', progress: 0, message: 'Falló', cancel_requested: false, error: { code: 'render_failed', message: 'FFmpeg rechazó el archivo', retryable: true }, download_url: null })
    render(<ExportPanel projectId="project" hasMedia hasTranscript flushPending={vi.fn().mockResolvedValue(undefined)} getAssLayout={() => []} />)
    fireEvent.change(screen.getByLabelText('Formato de exportación'), { target: { value: 'mp4' } })
    fireEvent.click(screen.getByRole('button', { name: 'Generar archivo' }))
    expect((await screen.findByRole('alert')).textContent).toContain('FFmpeg rechazó el archivo')
    expect(screen.queryByText(/%/)).toBeNull()
  })
})
