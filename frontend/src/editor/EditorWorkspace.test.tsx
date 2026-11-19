import { act, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectDocument } from '../api/client'
import { EditorWorkspace, type EditorWorkspaceHandle } from './EditorWorkspace'
import { buildAssLayout } from './exportLayout'
import { formatSubtitleText, normalizeDesign } from './model'

const api = vi.hoisted(() => ({ update: vi.fn() }))
vi.mock('../api/client', () => ({
  updateProjectDocument: api.update,
  mediaUrl: (projectId: string) => `/api/v1/projects/${projectId}/media`,
  audioUrl: (projectId: string) => `/api/v1/projects/${projectId}/audio`,
}))

const originalCurrentTime = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'currentTime')
const originalDuration = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'duration')
const originalPaused = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'paused')
const originalPlay = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'play')
const originalPause = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'pause')
const originalLoad = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'load')
const originalVideoWidth = Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype, 'videoWidth')
const originalVideoHeight = Object.getOwnPropertyDescriptor(HTMLVideoElement.prototype, 'videoHeight')
const originalCanvasContext = Object.getOwnPropertyDescriptor(HTMLCanvasElement.prototype, 'getContext')

function projectFixture(document: Record<string, unknown> = {}): ProjectDocument {
  const transcript = {
    language: 'es',
    segments: [{
      id: 'seg-stable', start: 0, end: 1.2, text: 'Hola mundo hoy',
      words: [
        { id: 'word-hola', text: 'Hola', start: 0, end: 0.4 },
        { id: 'word-mundo', text: ' mundo', start: 0.4, end: 0.9 },
        { id: 'word-hoy', text: ' hoy', start: 0.9, end: 1.2 },
      ],
    }],
  }
  return {
    id: 'project-test', name: 'Proyecto de prueba', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    metadata: {}, document: { transcription: transcript, ...document },
    audio: null,
    media: { filename: 'test.mp4', duration: 10, width: 1080, height: 1920, fps: 30, video_codec: 'h264', audio_codec: 'aac', has_audio: true, probe_error: null },
    transcription: transcript,
    transcription_info: { filename: 'test.json', language: 'es', segment_count: 1, word_count: 3 },
  }
}

function videoElement(): HTMLVideoElement {
  return screen.getByLabelText('Reproductor de video') as HTMLVideoElement
}

describe('editor visual interactions', () => {
  beforeEach(() => {
    const times = new WeakMap<HTMLMediaElement, number>()
    const playing = new WeakMap<HTMLMediaElement, boolean>()
    Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', {
      configurable: true,
      get() { return times.get(this) ?? 0 },
      set(value: number) { times.set(this, value) },
    })
    Object.defineProperty(HTMLMediaElement.prototype, 'duration', { configurable: true, get: () => 10 })
    Object.defineProperty(HTMLMediaElement.prototype, 'paused', { configurable: true, get() { return !(playing.get(this) ?? false) } })
    Object.defineProperty(HTMLMediaElement.prototype, 'play', { configurable: true, value: function(this: HTMLMediaElement) {
      playing.set(this, true)
      this.dispatchEvent(new Event('play'))
      return Promise.resolve()
    } })
    Object.defineProperty(HTMLMediaElement.prototype, 'pause', { configurable: true, value: function(this: HTMLMediaElement) {
      playing.set(this, false)
      this.dispatchEvent(new Event('pause'))
    } })
    Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', { configurable: true, get: () => 1080 })
    Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', { configurable: true, get: () => 1920 })
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      configurable: true,
      value: () => ({ font: '', measureText: (text: string) => ({ width: text.length * 8 }) }),
    })
    api.update.mockResolvedValue({})
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    if (originalCurrentTime) Object.defineProperty(HTMLMediaElement.prototype, 'currentTime', originalCurrentTime)
    if (originalDuration) Object.defineProperty(HTMLMediaElement.prototype, 'duration', originalDuration)
    if (originalPaused) Object.defineProperty(HTMLMediaElement.prototype, 'paused', originalPaused)
    if (originalPlay) Object.defineProperty(HTMLMediaElement.prototype, 'play', originalPlay)
    if (originalPause) Object.defineProperty(HTMLMediaElement.prototype, 'pause', originalPause)
    if (originalLoad) Object.defineProperty(HTMLMediaElement.prototype, 'load', originalLoad)
    if (originalVideoWidth) Object.defineProperty(HTMLVideoElement.prototype, 'videoWidth', originalVideoWidth)
    if (originalVideoHeight) Object.defineProperty(HTMLVideoElement.prototype, 'videoHeight', originalVideoHeight)
    if (originalCanvasContext) Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', originalCanvasContext)
  })

  it('persists phrase edits alongside the canonical transcript without changing original word timestamps', async () => {
    vi.useFakeTimers()
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Frases' }))
    fireEvent.change(screen.getByLabelText('Texto F001'), { target: { value: 'Texto de frase editado' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const saved = api.update.mock.calls.at(-1)?.[1] as Record<string, any>
    expect(saved.phrase_transcription.phrases[0]).toMatchObject({ id: 'F001', text: 'Texto de frase editado', wordIds: ['word-hola', 'word-mundo', 'word-hoy'] })
    expect(saved.transcription.segments[0].words.map((word: any) => [word.start, word.end])).toEqual([[0, 0.4], [0.4, 0.9], [0.9, 1.2]])
  })

  it('plays a phrase from its start and pauses at its final timestamp', () => {
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Frases' }))
    const video = videoElement()
    fireEvent.click(screen.getByRole('button', { name: 'Reproducir frase' }))
    expect(video.currentTime).toBe(0)
    expect(video.paused).toBe(false)
    video.currentTime = 1.2
    fireEvent.timeUpdate(video)
    expect(video.paused).toBe(true)
  })

  it('changes the phrase audio URL and reloads it when the project audio is replaced from 8 to 40 seconds', () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const initial = {
      ...projectFixture(),
      audio: { filename: 'voz.wav', duration: 8, sample_rate: 48000, channels: 2, codec: 'pcm_s16le', format_name: 'wav', relative_path: 'project-test/media/voz.wav' },
    }
    const { rerender } = render(<EditorWorkspace project={initial} onStatus={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Frases' }))
    const player = screen.getByLabelText('Reproductor de audio de frases')
    expect(player.getAttribute('src')).toContain('?v=2026-01-01T00%3A00%3A00Z')

    const replaced = {
      ...initial,
      updated_at: '2026-01-01T00:00:01Z',
      audio: { ...initial.audio, filename: 'voz-nueva.wav', duration: 40, relative_path: 'project-test/media/voz-nueva.wav' },
    }
    rerender(<EditorWorkspace project={replaced} onStatus={() => undefined} />)

    const replacementPlayer = screen.getByLabelText('Reproductor de audio de frases')
    expect(replacementPlayer.getAttribute('src')).toContain('?v=2026-01-01T00%3A00%3A01Z')
    expect(replacementPlayer).not.toBe(player)
    expect(load).toHaveBeenCalledTimes(2)
    expect(pause).toHaveBeenCalled()
  })

  it('selects a word, seeks the player and synchronizes the timeline playhead', () => {
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const video = videoElement()
    fireEvent.click(screen.getByRole('button', { name: '02' }))
    expect(video.currentTime).toBe(0.4)
    expect(document.querySelector('.timeline-playhead')?.getAttribute('style')).toContain('7.2px')
    expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('mundo')

    const track = document.querySelector('.timeline-track')!
    fireEvent.pointerDown(track, { clientX: 36, pointerId: 1 })
    expect(video.currentTime).toBe(2)

    video.currentTime = 0.95
    fireEvent.timeUpdate(video)
    video.currentTime = 0.1
    fireEvent.timeUpdate(video)
    expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('Hola')
    expect(document.querySelector('.timeline-playhead')?.getAttribute('style')).toContain('1.8px')
  })

  it('updates active word highlighting during playback and clears the caption outside the segment', () => {
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const video = videoElement()
    video.currentTime = 0.95
    fireEvent.timeUpdate(video)
    expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('hoy')

    video.currentTime = 1.3
    fireEvent.timeUpdate(video)
    expect(screen.queryByTestId('subtitle-preview')).toBeNull()
    fireEvent.pause(video)
    expect(video.currentTime).toBe(1.3)
  })

  it('edits text and timestamp values and autosaves them into the project document', async () => {
    vi.useFakeTimers()
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Texto palabra 2'), { target: { value: ' planeta' } })
    const end = screen.getByLabelText('Final palabra 2')
    fireEvent.focus(end)
    fireEvent.change(end, { target: { value: '0.85' } })
    fireEvent.blur(end)
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })

    expect(api.update).toHaveBeenCalledTimes(1)
    const savedDocument = api.update.mock.calls[0][1] as Record<string, any>
    expect(savedDocument.transcription.segments[0].words[1]).toMatchObject({ id: 'word-mundo', text: ' planeta', end: 0.85 })
  })

  it('applies design colors and normalized position to the live subtitle preview', () => {
    vi.useFakeTimers()
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Palabra activa'), { target: { value: '#ffff00' } })
    fireEvent.change(screen.getByRole('slider', { name: /Posición X/ }), { target: { value: '80' } })
    const activeWord = screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]') as HTMLElement
    expect(activeWord.style.color).toBe('rgb(255, 255, 0)')
    expect(activeWord.classList.contains('subtitle-word--active')).toBe(true)
    expect(screen.getByTestId('subtitle-preview').style.color).toBe('rgb(255, 255, 255)')
    expect(screen.getByTestId('subtitle-preview').style.getPropertyValue('-webkit-text-stroke')).toContain('2px')
    expect(screen.getByTestId('subtitle-preview').querySelectorAll('.subtitle-line')).toHaveLength(1)
    expect(screen.getByRole('slider', { name: /Posición X/ })).toHaveProperty('value', '80')
    expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('left: 12%')
    return act(async () => {
      await vi.advanceTimersByTimeAsync(600)
      expect(api.update.mock.calls[0][1].design).toMatchObject({ activeWordColor: '#ffff00', positionX: 80 })
    })
  })

  it('composes the formatted Preview and export manifest from the same original word objects', () => {
    Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
      configurable: true,
      value: () => ({
        font: '',
        measureText: (text: string) => ({
          width: Array.from(text).reduce((width, character) => (
            width + (character === 'I' ? 2 : 1)
          ), 0) * 40,
        }),
      }),
    })
    const transcript = {
      language: 'es',
      segments: [{
        id: 'segment-visible', start: 0, end: 0.8, text: 'mi niña',
        words: [
          { id: 'word-mi', text: 'mi', start: 0, end: 0.3 },
          { id: 'word-nina', text: ' niña', start: 0.3, end: 0.8 },
        ],
      }],
    }
    const original = JSON.stringify(transcript)
    const design = { fontSize: 56, maxWidth: 30, wordSpacing: 0.16, textFormat: 'uppercase' }
    render(<EditorWorkspace project={projectFixture({ transcription: transcript, design })} onStatus={() => undefined} />)

    const previewLines = [...document.querySelectorAll('.subtitle-line')].map((line) => line.textContent)
    const manifest = buildAssLayout(
      transcript,
      normalizeDesign(design),
      1080,
      1080,
      (_size, text) => Array.from(text).reduce((width, character) => (
        width + (character === 'I' ? 2 : 1)
      ), 0) * 40,
    )

    expect(previewLines).toEqual(['MI', 'NIÑA'])
    expect(manifest[0].lines).toEqual([['word-mi'], ['word-nina']])
    const manifestLines = manifest[0].lines.map((line) => line.map((id) => (
      formatSubtitleText(
        transcript.segments[0].words.find((word) => word.id === id)?.text.trim() ?? '',
        'uppercase',
      )
    )).join(' '))
    expect(manifestLines).toEqual(previewLines)
    expect(JSON.stringify(transcript)).toBe(original)
  })

  it('flushes pending changes for project switching and restores them in a fresh editor instance', async () => {
    vi.useFakeTimers()
    const ref = createRef<EditorWorkspaceHandle>()
    const original = projectFixture()
    const { unmount } = render(<EditorWorkspace ref={ref} project={original} onStatus={() => undefined} />)
    fireEvent.change(screen.getByLabelText('Texto palabra 1'), { target: { value: 'Buenas' } })
    await act(async () => { await ref.current?.flushPending() })
    const stored = api.update.mock.calls[0][1] as Record<string, any>
    unmount()
    render(<EditorWorkspace project={projectFixture(stored)} onStatus={() => undefined} />)
    expect(screen.getByLabelText('Texto palabra 1')).toHaveProperty('value', 'Buenas')
  })

  it('does not mutate the transcript editor on every playhead tick within the same word', async () => {
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const transcriptList = document.querySelector('.transcript-editor')!
    const observer = new MutationObserver(() => undefined)
    observer.observe(transcriptList, { attributes: true, childList: true, subtree: true, characterData: true })
    const video = videoElement()
    video.currentTime = 0.1
    fireEvent.timeUpdate(video)
    video.currentTime = 0.2
    fireEvent.timeUpdate(video)
    await act(async () => { await Promise.resolve() })
    const changes = observer.takeRecords()
    observer.disconnect()
    expect(changes).toHaveLength(0)
  })

  it('keeps the video aspect ratio contained for vertical and horizontal project metadata', () => {
    const first = render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const verticalStage = document.querySelector('.video-stage') as HTMLDivElement
    expect(verticalStage.style.aspectRatio).toContain('0.5625')
    expect(videoElement().style.objectFit).toBe('contain')
    first.unmount()
    const horizontal = projectFixture()
    horizontal.media = { ...horizontal.media!, width: 1920, height: 1080 }
    render(<EditorWorkspace project={horizontal} onStatus={() => undefined} />)
    expect(Number.parseFloat((document.querySelector('.video-stage') as HTMLDivElement).style.aspectRatio)).toBeCloseTo(16 / 9)
  })

  it('applies a temporary position at the interval start, follows direct seeks, and restores base position at the end', () => {
    const project = projectFixture({ design: { temporalEvents: [
      { id: 'top-event', start: 0.5, end: 0.9, position: 'top', positionX: 50, positionY: 20 },
    ], positionY: 62 } })
    render(<EditorWorkspace project={project} onStatus={() => undefined} />)
    const video = videoElement()
    const overlay = screen.getByTestId('subtitle-preview')
    const seek = document.querySelector('.playback-status input')!
    fireEvent.change(seek, { target: { value: '0.6' } })
    expect(video.currentTime).toBe(0.6)
    expect(overlay.getAttribute('style')).toContain('20%')
    expect(document.querySelector('.timeline-position-event')).toBeTruthy()

    fireEvent.change(seek, { target: { value: '0.95' } })
    expect(video.currentTime).toBe(0.95)
    expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('62%')
  })

  it('adds, edits, deletes and autosaves temporal position events without changing base design', async () => {
    vi.useFakeTimers()
    const mounted = render(<EditorWorkspace project={projectFixture({ design: { positionY: 62 } })} onStatus={() => undefined} />)
    fireEvent.click(screen.getByRole('button', { name: 'Agregar cambio' }))
    fireEvent.change(screen.getByLabelText('Inicio cambio 1'), { target: { value: '30' } })
    fireEvent.change(screen.getByLabelText('Final cambio 1'), { target: { value: '36' } })
    fireEvent.change(screen.getByLabelText('Posición cambio 1'), { target: { value: 'top' } })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const stored = api.update.mock.calls.at(-1)![1] as Record<string, any>
    expect(stored.design).toMatchObject({ positionY: 62, temporalEvents: [{ start: 30, end: 36, position: 'top' }] })
    mounted.unmount()
    render(<EditorWorkspace project={projectFixture(stored)} onStatus={() => undefined} />)
    expect(screen.getByLabelText('Inicio cambio 1')).toHaveProperty('value', '30')
    expect(document.querySelector('.timeline-position-event')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar cambio 1' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    expect((api.update.mock.calls.at(-1)![1] as Record<string, any>).design.temporalEvents).toEqual([])
  })

  it('formats displayed words in Spanish, persists the format, and preserves source tokens and active color', async () => {
    vi.useFakeTimers()
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const original = screen.getByLabelText('Texto palabra 1') as HTMLInputElement
    fireEvent.change(screen.getByLabelText('Formato del texto'), { target: { value: 'uppercase' } })
    const active = screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]') as HTMLElement
    expect(active.textContent).toBe('HOLA')
    expect(active.style.color).toBe('rgb(55, 215, 213)')
    expect(original.value).toBe('Hola')
    fireEvent.change(screen.getByLabelText('Formato del texto'), { target: { value: 'lowercase' } })
    expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('hola')
    fireEvent.change(screen.getByLabelText('Formato del texto'), { target: { value: 'title' } })
    expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('Hola')
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const saved = api.update.mock.calls.at(-1)![1] as Record<string, any>
    expect(saved.design.textFormat).toBe('title')
    expect(saved.transcription.segments[0].words[0].text).toBe('Hola')
  })

  it('enters and exits fullscreen with the shared video and subtitle overlay while playback and seeking stay synchronized', async () => {
    const oldRequest = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'requestFullscreen')
    const oldExit = Object.getOwnPropertyDescriptor(document, 'exitFullscreen')
    const oldElement = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: null })
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: async function(this: HTMLElement) {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: this })
      document.dispatchEvent(new Event('fullscreenchange'))
    } })
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: async () => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: null })
      document.dispatchEvent(new Event('fullscreenchange'))
    } })
    try {
      const project = projectFixture({ design: { temporalEvents: [
        { id: 'fullscreen-top', start: 0.5, end: 0.9, position: 'top', positionX: 50, positionY: 20 },
      ] } })
      render(<EditorWorkspace project={project} onStatus={() => undefined} />)
      const video = videoElement()
      const stage = document.querySelector('.video-stage')!
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Entrar en pantalla completa' })) })
      expect(document.fullscreenElement).toBe(stage)
      expect(stage.contains(video)).toBe(true)
      expect(stage.contains(screen.getByTestId('subtitle-preview'))).toBe(true)
      fireEvent.click(screen.getByRole('button', { name: 'Reproducir video' }))
      expect(screen.getByRole('button', { name: 'Pausar video' })).toBeTruthy()
      fireEvent.change(document.querySelector('.playback-status input')!, { target: { value: '0.6' } })
      expect(video.currentTime).toBe(0.6)
      expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('20%')
      expect(screen.getByTestId('subtitle-preview').querySelector('[aria-current="true"]')?.textContent).toBe('mundo')
      fireEvent.change(document.querySelector('.playback-status input')!, { target: { value: '0.95' } })
      expect(video.currentTime).toBe(0.95)
      expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('62%')
      fireEvent.click(screen.getByRole('button', { name: 'Pausar video' }))
      expect(screen.getByRole('button', { name: 'Reproducir video' })).toBeTruthy()
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Salir de pantalla completa' })) })
      expect(document.fullscreenElement).toBeNull()
      expect(stage.contains(screen.getByTestId('subtitle-preview'))).toBe(true)
    } finally {
      if (oldRequest) Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', oldRequest)
      else delete (HTMLElement.prototype as Partial<HTMLElement>).requestFullscreen
      if (oldExit) Object.defineProperty(document, 'exitFullscreen', oldExit)
      else delete (document as Partial<Document>).exitFullscreen
      if (oldElement) Object.defineProperty(document, 'fullscreenElement', oldElement)
      else Reflect.deleteProperty(document, 'fullscreenElement')
    }
  })

  it('recomposes from observed video dimensions after fullscreen and keeps selection from changing its font size', async () => {
    let resizeCallback: ResizeObserverCallback = () => undefined
    const disconnect = vi.fn()
    class ResizeObserverMock {
      constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
      observe = vi.fn()
      unobserve = vi.fn()
      disconnect = disconnect
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)

    const oldRequest = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'requestFullscreen')
    const oldExit = Object.getOwnPropertyDescriptor(document, 'exitFullscreen')
    const oldElement = Object.getOwnPropertyDescriptor(document, 'fullscreenElement')
    Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: null })
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: async function(this: HTMLElement) {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: this })
      document.dispatchEvent(new Event('fullscreenchange'))
    } })
    Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: async () => {
      Object.defineProperty(document, 'fullscreenElement', { configurable: true, writable: true, value: null })
      document.dispatchEvent(new Event('fullscreenchange'))
    } })

    const makeRect = (width: number, height: number): DOMRect => ({
      x: 0, y: 0, left: 0, top: 0, right: width, bottom: height, width, height,
      toJSON: () => ({}),
    }) as DOMRect
    const project = projectFixture({ design: { fontFamily: 'Anton', fontSize: 86, maxWidth: 100 } })
    const view = render(<EditorWorkspace project={project} onStatus={() => undefined} />)
    const stage = document.querySelector('.video-stage') as HTMLElement
    const video = videoElement()

    try {
      Object.defineProperty(video, 'getBoundingClientRect', { configurable: true, value: () => makeRect(376, 668.444) })
      await act(async () => { resizeCallback([], {} as ResizeObserver) })
      const normalFontSize = 86 * 376 / 1080
      expect(Number.parseFloat(screen.getByTestId('subtitle-preview').style.fontSize)).toBeCloseTo(normalFontSize)

      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Entrar en pantalla completa' })) })
      Object.defineProperty(video, 'getBoundingClientRect', { configurable: true, value: () => makeRect(800, 544) })
      await act(async () => { resizeCallback([], {} as ResizeObserver) })
      expect(Number.parseFloat(screen.getByTestId('subtitle-preview').style.fontSize)).toBeCloseTo(86 * 306 / 1080)

      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Salir de pantalla completa' })) })
      Object.defineProperty(video, 'getBoundingClientRect', { configurable: true, value: () => makeRect(376, 668.444) })
      await act(async () => { resizeCallback([], {} as ResizeObserver) })
      const caption = screen.getByTestId('subtitle-preview')
      expect(Number.parseFloat(caption.style.fontSize)).toBeCloseTo(normalFontSize)

      fireEvent.pointerDown(caption, { pointerId: 31, clientX: 20, clientY: 20 })
      expect(caption.className).toContain('subtitle-preview--selected')
      expect(Number.parseFloat(caption.style.fontSize)).toBeCloseTo(normalFontSize)
      fireEvent.pointerDown(stage, { pointerId: 32, clientX: 1, clientY: 1 })
      expect(screen.getByTestId('subtitle-preview').className).not.toContain('subtitle-preview--selected')
      expect(Number.parseFloat(screen.getByTestId('subtitle-preview').style.fontSize)).toBeCloseTo(normalFontSize)
      expect(disconnect).not.toHaveBeenCalled()
    } finally {
      view.unmount()
      expect(disconnect).toHaveBeenCalled()
      if (oldRequest) Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', oldRequest)
      else delete (HTMLElement.prototype as Partial<HTMLElement>).requestFullscreen
      if (oldExit) Object.defineProperty(document, 'exitFullscreen', oldExit)
      else delete (document as Partial<Document>).exitFullscreen
      if (oldElement) Object.defineProperty(document, 'fullscreenElement', oldElement)
      else Reflect.deleteProperty(document, 'fullscreenElement')
    }
  })

  it('selects and drags the base caption with normalized coordinates and autosaves X/Y', async () => {
    vi.useFakeTimers()
    render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const stage = document.querySelector('.video-stage') as HTMLElement
    Object.defineProperty(stage, 'getBoundingClientRect', { configurable: true, value: () => ({ left: 0, top: 0, right: 500, bottom: 900, width: 500, height: 900, x: 0, y: 0, toJSON: () => ({}) }) })
    const caption = screen.getByTestId('subtitle-preview')
    fireEvent.pointerDown(caption, { pointerId: 8, clientX: 100, clientY: 100 })
    expect(caption.className).toContain('subtitle-preview--selected')
    fireEvent.pointerMove(caption, { pointerId: 8, clientX: 100, clientY: 190 })
    expect((screen.getByRole('slider', { name: 'Posición Y' }) as HTMLInputElement).value).toBe('72')
    expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('72%')
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const savedDesign = api.update.mock.calls.at(-1)?.[1].design
    expect(savedDesign.positionY).toBeCloseTo(72)
    fireEvent.pointerDown(stage, { pointerId: 9, clientX: 20, clientY: 20 })
    expect(screen.getByTestId('subtitle-preview').className).not.toContain('subtitle-preview--selected')
  })

  it('keeps guides visible with snap disabled and removes them when dragging ends', () => {
    const view = render(<EditorWorkspace project={projectFixture()} onStatus={() => undefined} />)
    const content = document.querySelector('.subtitle-video-content') as HTMLDivElement
    const caption = screen.getByTestId('subtitle-preview')
    const makeRect = (left: number, top: number, width: number, height: number): DOMRect => ({
      x: left, y: top, left, top, right: left + width, bottom: top + height, width, height,
      toJSON: () => ({}),
    }) as DOMRect
    Object.defineProperty(content, 'getBoundingClientRect', {
      configurable: true, value: () => makeRect(100, 200, 400, 700),
    })
    Object.defineProperty(caption, 'getBoundingClientRect', {
      configurable: true, value: () => makeRect(200, 400, 200, 80),
    })

    fireEvent.pointerDown(caption, { pointerId: 71, clientX: 300, clientY: 440 })
    expect(document.querySelectorAll('.subtitle-smart-guide')).toHaveLength(0)
    fireEvent.pointerMove(caption, { pointerId: 71, clientX: 330, clientY: 540 })
    expect(document.querySelectorAll('.subtitle-smart-guide')).toHaveLength(0)
    fireEvent.pointerMove(caption, { pointerId: 71, clientX: 303, clientY: 440 })
    expect(document.querySelectorAll('.subtitle-smart-guide')).toHaveLength(1)
    expect(document.querySelector('.subtitle-smart-guide--x.is-active')).not.toBeNull()
    expect(document.querySelector('.subtitle-smart-guide--y')).toBeNull()
    fireEvent.pointerMove(caption, { pointerId: 71, clientX: 303, clientY: 550 })
    expect(document.querySelectorAll('.subtitle-smart-guide')).toHaveLength(2)
    expect(document.querySelector('.subtitle-smart-guide--y.subtitle-smart-guide--center.is-active')).not.toBeNull()

    const snapToggle = screen.getByRole('button', { name: 'Snap activado' })
    fireEvent.click(snapToggle)
    expect(screen.getByRole('button', { name: 'Snap desactivado' }).getAttribute('aria-pressed')).toBe('false')
    fireEvent.pointerMove(caption, { pointerId: 71, clientX: 304, clientY: 550 })
    expect(document.querySelectorAll('.subtitle-smart-guide')).toHaveLength(2)
    expect(Number((screen.getByRole('slider', { name: 'Posición X' }) as HTMLInputElement).value)).toBeCloseTo(50 + (4 / 400 * 100) / 0.15)
    expect(document.querySelector('.subtitle-guide-distances')?.textContent).toContain('Izq')
    expect(document.querySelector('.subtitle-guide-distances')?.textContent).toContain('Sup')

    fireEvent.pointerUp(caption, { pointerId: 71 })
    expect(document.querySelector('.subtitle-smart-guides')).toBeNull()
    view.unmount()
  })

  it('positions and drags the caption in the contained vertical-video area without changing alignment semantics', async () => {
    vi.useFakeTimers()
    let resizeCallback: ResizeObserverCallback = () => undefined
    class ResizeObserverMock {
      constructor(callback: ResizeObserverCallback) { resizeCallback = callback }
      observe = vi.fn()
      disconnect = vi.fn()
    }
    vi.stubGlobal('ResizeObserver', ResizeObserverMock)
    const makeRect = (left: number, top: number, width: number, height: number): DOMRect => ({
      x: left, y: top, left, top, right: left + width, bottom: top + height, width, height,
      toJSON: () => ({}),
    }) as DOMRect
    const design = { positionX: 37.4234, positionY: 66.3829, maxWidth: 53, alignment: 'left' as const }
    const view = render(<EditorWorkspace project={projectFixture({ design })} onStatus={() => undefined} />)
    const stage = document.querySelector('.video-stage') as HTMLElement
    const video = videoElement()
    const caption = screen.getByTestId('subtitle-preview')

    try {
      Object.defineProperty(stage, 'getBoundingClientRect', { configurable: true, value: () => makeRect(0, 0, 800, 544) })
      Object.defineProperty(video, 'getBoundingClientRect', { configurable: true, value: () => makeRect(0, 0, 800, 544) })
      await act(async () => { resizeCallback([], {} as ResizeObserver) })

      const content = document.querySelector('.subtitle-video-content') as HTMLDivElement
      expect(content.style.left).toBe('247px')
      expect(content.style.top).toBe('0px')
      expect(content.style.width).toBe('306px')
      expect(content.style.height).toBe('544px')
      Object.defineProperty(content, 'getBoundingClientRect', { configurable: true, value: () => makeRect(247, 0, 306, 544) })

      const alignment = screen.getAllByRole('combobox').find((item) => (item as HTMLSelectElement).value === 'left') as HTMLSelectElement
      expect(caption.style.getPropertyValue('--subtitle-alignment')).toBe('flex-start')
      fireEvent.change(alignment, { target: { value: 'center' } })
      expect(screen.getByTestId('subtitle-preview').style.getPropertyValue('--subtitle-alignment')).toBe('center')
      fireEvent.change(alignment, { target: { value: 'right' } })
      expect(screen.getByTestId('subtitle-preview').style.getPropertyValue('--subtitle-alignment')).toBe('flex-end')
      fireEvent.change(alignment, { target: { value: 'left' } })

      fireEvent.pointerDown(caption, { pointerId: 51, clientX: 300, clientY: 300 })
      fireEvent.pointerMove(caption, { pointerId: 51, clientX: 330, clientY: 327.2 })
      expect(Number((document.querySelector('input[type="range"][aria-label$="X"]') as HTMLInputElement).value)).toBeCloseTo(
        design.positionX + (30 / 306 * 100) / 0.47,
      )
      expect(Number((document.querySelector('input[type="range"][aria-label$="Y"]') as HTMLInputElement).value)).toBeCloseTo(71.3829)

      await act(async () => { await vi.advanceTimersByTimeAsync(600) })
      expect(api.update.mock.calls.at(-1)?.[1].design).toMatchObject({
        positionX: expect.closeTo(design.positionX + (30 / 306 * 100) / 0.47),
        positionY: expect.closeTo(71.3829),
      })
    } finally {
      view.unmount()
    }
  })

  it('moves only the active temporal event and leaves the base and other events unchanged', async () => {
    vi.useFakeTimers()
    const design = {
      positionX: 50, positionY: 62,
      temporalEvents: [
        { id: 'event-active', start: 0, end: 2, position: 'top', positionX: 50, positionY: 20 },
        { id: 'event-other', start: 3, end: 5, position: 'bottom', positionX: 50, positionY: 82 },
      ],
    }
    render(<EditorWorkspace project={projectFixture({ design })} onStatus={() => undefined} />)
    const stage = document.querySelector('.video-stage') as HTMLElement
    Object.defineProperty(stage, 'getBoundingClientRect', { configurable: true, value: () => ({ left: 0, top: 0, right: 500, bottom: 900, width: 500, height: 900, x: 0, y: 0, toJSON: () => ({}) }) })
    const caption = screen.getByTestId('subtitle-preview')
    fireEvent.pointerDown(caption, { pointerId: 3, clientX: 50, clientY: 50 })
    fireEvent.pointerMove(caption, { pointerId: 3, clientX: 50, clientY: 140 })
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const savedDesign = api.update.mock.calls.at(-1)?.[1].design
    expect(savedDesign.positionY).toBe(62)
    expect(savedDesign.temporalEvents[0].position).toBe('custom')
    expect(savedDesign.temporalEvents[0].positionY).toBeCloseTo(30)
    expect(savedDesign.temporalEvents[1]).toMatchObject({ id: 'event-other', position: 'bottom', positionY: 82 })
  })

  it('drags horizontal video captions and applies manual X/Y changes to the active event immediately', async () => {
    vi.useFakeTimers()
    const project = projectFixture({ design: { temporalEvents: [
      { id: 'event-horizontal', start: 0, end: 2, position: 'custom', positionX: 40, positionY: 45 },
    ] } })
    project.media = { ...project.media!, width: 1920, height: 1080 }
    render(<EditorWorkspace project={project} onStatus={() => undefined} />)
    const stage = document.querySelector('.video-stage') as HTMLElement
    Object.defineProperty(stage, 'getBoundingClientRect', { configurable: true, value: () => ({ left: 0, top: 0, right: 900, bottom: 506.25, width: 900, height: 506.25, x: 0, y: 0, toJSON: () => ({}) }) })
    const caption = screen.getByTestId('subtitle-preview')
    fireEvent.pointerDown(caption, { pointerId: 5, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(caption, { pointerId: 5, clientX: 145, clientY: 100 })
    const horizontalStyle = screen.getByTestId('subtitle-preview').getAttribute('style')
    expect(horizontalStyle).toContain('45%')
    fireEvent.change(screen.getByRole('slider', { name: 'Posición Y' }), { target: { value: '55' } })
    expect(screen.getByTestId('subtitle-preview').getAttribute('style')).toContain('55%')
    await act(async () => { await vi.advanceTimersByTimeAsync(600) })
    const savedDesign = api.update.mock.calls.at(-1)?.[1].design
    expect(savedDesign.positionY).toBe(62)
    expect(savedDesign.temporalEvents[0].position).toBe('custom')
    expect(savedDesign.temporalEvents[0].positionY).toBe(55)
    expect(savedDesign.temporalEvents[0].positionX).toBeGreaterThan(40)
  })
})
