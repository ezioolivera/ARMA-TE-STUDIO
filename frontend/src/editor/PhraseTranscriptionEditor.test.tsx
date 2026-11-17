import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { normalizeTranscript } from './model'
import { PhraseTranscriptionEditor } from './PhraseTranscriptionEditor'

const transcript = normalizeTranscript({ language: 'es', segments: [{ start: 0, end: 2, text: '', words: [
  { id: 'w1', text: 'Hola', start: 0, end: 0.5 },
  { id: 'w2', text: ' mundo.', start: 0.6, end: 1.2 },
  { id: 'w3', text: ' Otra', start: 1.3, end: 1.7 },
  { id: 'w4', text: ' vez.', start: 1.8, end: 2 },
] }] })

describe('phrase editor controls', () => {
  it('reloads the audio element when a replacement URL and duration arrive', () => {
    const load = vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => undefined)
    const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => undefined)
    const props = {
      projectId: 'project-1', projectName: 'Proyecto', audioId: 'voz.wav',
      transcript, storedValue: null, onChange: vi.fn(), flushPending: vi.fn().mockResolvedValue(undefined),
      onPlayVideoPhrase: vi.fn(), onSeekVideoPhrase: vi.fn(), onStopVideoPlayback: vi.fn(), canPlayPhrase: true,
    }
    const { rerender } = render(<PhraseTranscriptionEditor
      {...props} audioDuration={8} audioSrc="/audio/project-1?v=before-replacement"
    />)
    expect(screen.getByLabelText('Reproductor de audio de frases').getAttribute('src'))
      .toBe('/audio/project-1?v=before-replacement')

    rerender(<PhraseTranscriptionEditor
      {...props} audioDuration={40} audioSrc="/audio/project-1?v=after-replacement"
    />)

    expect(screen.getByLabelText('Reproductor de audio de frases').getAttribute('src'))
      .toBe('/audio/project-1?v=after-replacement')
    expect(load).toHaveBeenCalledTimes(2)
    expect(pause).toHaveBeenCalled()
  })

  it('divides and joins phrases, edits text and starts phrase playback at its timestamps', () => {
    const onChange = vi.fn()
    const onPlayVideoPhrase = vi.fn()
    render(<PhraseTranscriptionEditor
      projectId="project-1" projectName="Proyecto" audioId="video.mp4" audioDuration={2}
      audioSrc={null} transcript={transcript} storedValue={null} onChange={onChange}
      flushPending={vi.fn().mockResolvedValue(undefined)} onPlayVideoPhrase={onPlayVideoPhrase}
      onSeekVideoPhrase={vi.fn()} onStopVideoPlayback={vi.fn()} canPlayPhrase
    />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Reproducir frase' })[0])
    expect(onPlayVideoPhrase).toHaveBeenCalledWith(0, 1.2)

    fireEvent.change(screen.getByLabelText('Texto F001'), { target: { value: 'Hola mundo' } })
    expect(onChange.mock.calls.at(-1)?.[0].phrases[0].text).toBe('Hola mundo')

    fireEvent.change(screen.getByLabelText('Punto de división F001'), { target: { value: '1' } })
    fireEvent.click(screen.getAllByRole('button', { name: 'Dividir' })[0])
    expect(screen.getAllByText(/^F00[1-3]$/)).toHaveLength(3)
    fireEvent.click(screen.getAllByRole('button', { name: 'Unir con la siguiente' })[0])
    expect(screen.getAllByText(/^F00[1-2]$/)).toHaveLength(2)
  })
})
