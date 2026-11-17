import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type { ProjectDocument, ProjectSummary } from './api/client'

const api = vi.hoisted(() => ({
  getHealth: vi.fn(), listProjects: vi.fn(), openProject: vi.fn(), createProject: vi.fn(),
  updateProjectDocument: vi.fn(), deleteProject: vi.fn(), importProjectFile: vi.fn(),
  startTranscriptionJob: vi.fn(), getTranscriptionJob: vi.fn(), events: [] as string[],
}))

vi.mock('./api/client', () => ({
  ApiError: class ApiError extends Error { constructor(message: string, readonly status: number) { super(message) } },
  getHealth: api.getHealth,
  listProjects: api.listProjects,
  openProject: api.openProject,
  createProject: api.createProject,
  updateProjectDocument: api.updateProjectDocument,
  deleteProject: api.deleteProject,
  importProjectFile: api.importProjectFile,
  startTranscriptionJob: api.startTranscriptionJob,
  getTranscriptionJob: api.getTranscriptionJob,
  mediaUrl: (id: string) => `/media/${id}`,
}))

const emptyTranscript = {
  language: 'es',
  segments: [{
    id: 'seg-one', start: 0, end: 1, text: 'Hola',
    words: [{ id: 'word-one', text: 'Hola', start: 0, end: 1 }],
  }],
}

function documentFor(id: string, name: string): ProjectDocument {
  return {
    id, name, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    metadata: {}, document: { transcription: emptyTranscript },
    media: null, transcription: emptyTranscript,
    transcription_info: { filename: 'transcription.json', language: 'es', segment_count: 1, word_count: 1 },
  }
}

function summary(project: ProjectDocument): ProjectSummary {
  return {
    id: project.id, name: project.name, updated_at: project.updated_at,
    media: project.media, transcription_info: project.transcription_info,
  }
}

describe('application persistence boundaries', () => {
  beforeEach(() => {
    api.events.length = 0
    api.deleteProject.mockImplementation(async (id: string) => { api.events.push(`delete:${id}`); return { id, deleted: true } })
    const first = documentFor('project-1', 'Proyecto uno')
    const second = documentFor('project-2', 'Proyecto dos')
    api.getHealth.mockResolvedValue({ status: 'ok', application: 'ARMA-TE', version: '0.1.0' })
    api.listProjects.mockResolvedValue([summary(first), summary(second)])
    api.openProject.mockImplementation(async (id: string) => {
      api.events.push(`open:${id}`)
      return id === first.id ? first : second
    })
    api.updateProjectDocument.mockImplementation(async (id: string, document: Record<string, unknown>) => {
      api.events.push(`save:${id}`)
      return { ...(id === first.id ? first : second), document }
    })
  })

  it('flushes the current project before switching to another project', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getByText('Backend conectado')).toBeTruthy())
    fireEvent.click(document.querySelectorAll('.project-row')[0])
    await screen.findByLabelText('Texto palabra 1')
    fireEvent.change(screen.getByLabelText('Texto palabra 1'), { target: { value: 'Buenos días' } })
    fireEvent.click(document.querySelectorAll('.project-row')[1])
    await screen.findByText('Proyecto dos', { selector: 'h2' })

    expect(api.events.indexOf('save:project-1')).toBeGreaterThanOrEqual(0)
    expect(api.events.indexOf('save:project-1')).toBeLessThan(api.events.indexOf('open:project-2'))
    expect(api.updateProjectDocument.mock.calls[0][1].transcription.segments[0].words[0].text).toBe('Buenos días')
  })

  it('reloads a generated transcript into the active editor without a page reload', async () => {
    const initial: ProjectDocument = {
      ...documentFor('project-1', 'Proyecto uno'),
      document: {}, transcription: null, transcription_info: null,
      media: {
        filename: 'voz.mp4', duration: 12, width: 1080, height: 1920, fps: 30,
        video_codec: 'h264', audio_codec: 'aac', has_audio: true, probe_error: null,
      },
    }
    const generated: ProjectDocument = {
      ...initial,
      updated_at: '2026-01-02T00:00:00Z',
      document: { transcription: {
        language: 'es', language_probability: 0.98,
        segments: [{ start: 0, end: 1, text: 'Generada', words: [{ text: 'Generada', start: 0, end: 1 }] }],
      } },
      transcription_info: { filename: 'voz.json', language: 'es', segment_count: 1, word_count: 1 },
    }
    api.listProjects.mockResolvedValue([summary(initial)])
    api.openProject.mockResolvedValueOnce(initial).mockResolvedValueOnce(generated)
    api.startTranscriptionJob.mockResolvedValue({
      job_id: 'transcription-1', state: 'queued', progress: 0, message: 'En cola', cancel_requested: false, error: null,
    })
    api.getTranscriptionJob.mockResolvedValue({
      job_id: 'transcription-1', state: 'succeeded', progress: 100, message: 'Completada', cancel_requested: false, error: null,
    })
    render(<App />)
    await waitFor(() => expect(screen.getByText('Backend conectado')).toBeTruthy())
    fireEvent.click(document.querySelectorAll('.project-row')[0])
    await screen.findByRole('button', { name: 'Generar transcripción' })
    fireEvent.click(screen.getByRole('button', { name: 'Generar transcripción' }))
    await waitFor(() => expect(api.startTranscriptionJob).toHaveBeenCalledWith(
      'project-1', { model_size: 'small', language: 'es' },
    ))
    await screen.findByLabelText('Texto palabra 1', {}, { timeout: 2500 })
    expect(api.openProject).toHaveBeenCalledTimes(2)
    expect((screen.getByLabelText('Texto palabra 1') as HTMLInputElement).value).toBe('Generada')
    expect(screen.getByText('Transcripción generada y proyecto actualizado.')).toBeTruthy()
    expect(api.getTranscriptionJob).toHaveBeenCalledWith('transcription-1', expect.any(AbortSignal))
  })

  it('requires confirmation and leaves a project untouched when deletion is cancelled', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<App />)
    await waitFor(() => expect(screen.getByText('Backend conectado')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar proyecto Proyecto uno' }))
    expect(api.deleteProject).not.toHaveBeenCalled()
    expect(screen.getByText('Proyecto uno')).toBeTruthy()
  })

  it('flushes changes before deleting the open project and returns to the empty state', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const first = documentFor('project-1', 'Proyecto uno')
    const second = documentFor('project-2', 'Proyecto dos')
    api.listProjects.mockResolvedValueOnce([summary(first), summary(second)]).mockResolvedValueOnce([summary(second)])
    render(<App />)
    await waitFor(() => expect(screen.getByText('Backend conectado')).toBeTruthy())
    fireEvent.click(document.querySelectorAll('.project-row')[0])
    await screen.findByLabelText('Texto palabra 1')
    fireEvent.change(screen.getByLabelText('Texto palabra 1'), { target: { value: 'Buenas' } })
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar proyecto Proyecto uno' }))
    await screen.findByText('Abre o crea un proyecto')
    expect(api.events.indexOf('save:project-1')).toBeGreaterThanOrEqual(0)
    expect(api.events.indexOf('save:project-1')).toBeLessThan(api.events.indexOf('delete:project-1'))
    expect(screen.queryByRole('button', { name: 'Eliminar proyecto Proyecto uno' })).toBeNull()
  })
})
