import { useEffect, useMemo, useRef, useState } from 'react'
import type { Transcript } from './model'
import { downloadText, phrasesCsv, phrasesJson } from './phraseExports'
import {
  groupTranscriptIntoPhrases,
  mergePhrases,
  phraseDuration,
  phraseWords,
  restorePhraseTranscription,
  splitPhrase,
  updatePhrase,
  type Phrase,
  type PhraseTranscription,
} from './phrases'

interface Props {
  projectId: string
  projectName: string
  audioId: string | null
  audioDuration: number | null
  audioSrc: string | null
  transcript: Transcript
  storedValue: unknown
  onChange: (value: PhraseTranscription) => void
  flushPending: () => Promise<void>
  onPlayVideoPhrase: (start: number, end: number) => void
  onSeekVideoPhrase: (start: number) => void
  onStopVideoPlayback: () => void
  canPlayPhrase: boolean
}

export function PhraseTranscriptionEditor({
  projectId, projectName, audioId, audioDuration, audioSrc, transcript, storedValue,
  onChange, flushPending, onPlayVideoPhrase, onSeekVideoPhrase, onStopVideoPlayback, canPlayPhrase,
}: Props) {
  const [data, setData] = useState<PhraseTranscription>(() =>
    restorePhraseTranscription(storedValue, transcript) ?? groupTranscriptIntoPhrases(transcript),
  )
  const [exportError, setExportError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(() => data.phrases[0]?.id ?? null)
  const [splitIndexes, setSplitIndexes] = useState<Record<string, number>>({})
  const audioRef = useRef<HTMLAudioElement>(null)
  const phraseEndRef = useRef<number | null>(null)
  const words = useMemo(() => new Map(transcript.segments.flatMap((segment) => segment.words).map((word) => [word.id, word])), [transcript])

  useEffect(() => { onChange(data) }, [data, onChange])
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !audioSrc) return
    phraseEndRef.current = null
    audio.pause()
    audio.load()
    return () => {
      phraseEndRef.current = null
      audio.pause()
    }
  }, [audioSrc])
  useEffect(() => {
    setData((current) => restorePhraseTranscription(current, transcript)
      ?? groupTranscriptIntoPhrases(transcript, current.pauseThreshold))
  }, [transcript])

  const selectedIndex = data.phrases.findIndex((phrase) => phrase.id === selectedId)
  const selectedPhrase = selectedIndex >= 0 ? data.phrases[selectedIndex] : null

  function setNext(next: PhraseTranscription) {
    setData(next)
    if (!next.phrases.some((phrase) => phrase.id === selectedId)) setSelectedId(next.phrases[0]?.id ?? null)
  }

  function play(phrase: Phrase) {
    setSelectedId(phrase.id)
    const audio = audioRef.current
    if (!audio) {
      onPlayVideoPhrase(phrase.start, phrase.end)
      return
    }
    onStopVideoPlayback()
    phraseEndRef.current = phrase.end
    audio.pause()
    audio.currentTime = phrase.start
    void audio.play().catch(() => { phraseEndRef.current = null })
  }

  function onAudioTimeUpdate() {
    const audio = audioRef.current
    const end = phraseEndRef.current
    if (audio && end !== null && audio.currentTime >= end) {
      audio.pause()
      phraseEndRef.current = null
    }
  }

  async function exportJson() {
    try {
      await flushPending()
      downloadText(`${projectName}-frases.json`, phrasesJson(projectId, audioId, audioDuration, transcript, data), 'application/json;charset=utf-8')
      setExportError('')
    } catch (error) { setExportError(error instanceof Error ? error.message : 'No se pudo exportar el JSON.') }
  }

  async function exportCsv() {
    try {
      await flushPending()
      downloadText(`${projectName}-frases.csv`, phrasesCsv(data), 'text/csv;charset=utf-8')
      setExportError('')
    } catch (error) { setExportError(error instanceof Error ? error.message : 'No se pudo exportar el CSV.') }
  }

  function split(phrase: Phrase) {
    const wordIndex = splitIndexes[phrase.id] ?? Math.floor(phrase.wordIds.length / 2)
    const next = splitPhrase(data, phrase.id, wordIndex, transcript)
    const index = data.phrases.findIndex((item) => item.id === phrase.id)
    setData(next)
    setSelectedId(next.phrases[index + 1]?.id ?? phrase.id)
  }

  return <section className="phrase-transcription-panel" aria-label="Transcripción por frases">
    <div className="phrase-transcription-heading">
      <div><p className="eyebrow">ANÁLISIS TEMPORAL</p><h3>Transcripción por frases</h3></div>
      <div className="phrase-export-actions">
        <button type="button" className="subtle-button" onClick={() => void exportJson()}>Exportar JSON</button>
        <button type="button" className="subtle-button" onClick={() => void exportCsv()}>Exportar CSV</button>
      </div>
    </div>
    <div className="phrase-tools">
      <label>Umbral de pausa (s)
        <input aria-label="Umbral de pausa" type="number" min="0.1" max="10" step="0.1" value={data.pauseThreshold}
          onChange={(event) => {
            const value = Number(event.target.value)
            if (Number.isFinite(value) && value >= 0.1 && value <= 10) setData((current) => ({ ...current, pauseThreshold: value }))
          }} />
      </label>
      <button type="button" className="subtle-button" onClick={() => {
        if (window.confirm('Volver a agrupar reemplazará las divisiones y textos manuales actuales. ¿Continuar?')) {
          const next = groupTranscriptIntoPhrases(transcript, data.pauseThreshold)
          setData(next)
          setSelectedId(next.phrases[0]?.id ?? null)
        }
      }}>Reagrupar automáticamente</button>
      {selectedPhrase && <div className="phrase-navigation">
        <button type="button" className="subtle-button" disabled={selectedIndex <= 0}
          onClick={() => setSelectedId(data.phrases[selectedIndex - 1]?.id ?? null)}>Frase anterior</button>
        <span>{selectedIndex + 1} / {data.phrases.length}</span>
        <button type="button" className="subtle-button" disabled={selectedIndex >= data.phrases.length - 1}
          onClick={() => setSelectedId(data.phrases[selectedIndex + 1]?.id ?? null)}>Siguiente frase</button>
      </div>}
    </div>
    {audioSrc && <audio
      key={audioSrc} ref={audioRef} className="phrase-audio-player" controls preload="metadata" src={audioSrc}
      onTimeUpdate={onAudioTimeUpdate} onEnded={() => { phraseEndRef.current = null }}
      aria-label="Reproductor de audio de frases"
    />}
    {exportError && <p className="editor-error" role="alert">{exportError}</p>}
    <div className="phrase-list">
      {data.phrases.map((phrase) => {
        const phraseOriginalWords = phraseWords(phrase, transcript)
        const splitAfter = splitIndexes[phrase.id] ?? Math.max(1, Math.floor(phrase.wordIds.length / 2))
        return <article className={`phrase-card${phrase.id === selectedId ? ' phrase-card--selected' : ''}`} key={phrase.id}
          aria-label={`Frase ${phrase.id}`} onClick={() => {
            setSelectedId(phrase.id)
            if (audioRef.current) { audioRef.current.currentTime = phrase.start; phraseEndRef.current = null }
            else onSeekVideoPhrase(phrase.start)
          }}>
          <header><strong>{phrase.id}</strong><button type="button" className="subtle-button" disabled={!canPlayPhrase} onClick={(event) => { event.stopPropagation(); play(phrase) }}>Reproducir frase</button></header>
          <label>Texto
            <textarea aria-label={`Texto ${phrase.id}`} value={phrase.text} rows={2}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => setNext(updatePhrase(data, phrase.id, { text: event.target.value }))} />
          </label>
          <div className="phrase-time-fields">
            <label>Inicio (s)<input aria-label={`Inicio ${phrase.id}`} type="number" min="0" step="0.01" value={phrase.start}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => setNext(updatePhrase(data, phrase.id, { start: Number(event.target.value) }))} /></label>
            <label>Final (s)<input aria-label={`Final ${phrase.id}`} type="number" min="0" step="0.01" value={phrase.end}
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => setNext(updatePhrase(data, phrase.id, { end: Number(event.target.value) }))} /></label>
            <span>Duración: {phraseDuration(phrase).toFixed(2)} s</span>
          </div>
          <details><summary>Palabras originales ({phraseOriginalWords.length})</summary>
            <p>{phraseOriginalWords.map((word) => `${word.text.trim()} [${word.start.toFixed(2)}–${word.end.toFixed(2)}]`).join(' ')}</p>
          </details>
          <footer className="phrase-edit-actions">
            {phrase.wordIds.length > 1 && <>
              <label>Dividir después de
                <select aria-label={`Punto de división ${phrase.id}`} value={splitAfter}
                  onClick={(event) => event.stopPropagation()}
                  onChange={(event) => setSplitIndexes((current) => ({ ...current, [phrase.id]: Number(event.target.value) }))}>
                  {phrase.wordIds.slice(1).map((wordId, index) => <option key={wordId} value={index + 1}>{words.get(wordId)?.text.trim() ?? `palabra ${index + 2}`}</option>)}
                </select>
              </label>
              <button type="button" className="subtle-button" onClick={(event) => { event.stopPropagation(); split(phrase) }}>Dividir</button>
            </>}
            <button type="button" className="subtle-button" disabled={data.phrases[data.phrases.length - 1]?.id === phrase.id}
              onClick={(event) => { event.stopPropagation(); setNext(mergePhrases(data, phrase.id)) }}>Unir con la siguiente</button>
          </footer>
        </article>
      })}
      {data.phrases.length === 0 && <p className="muted">No hay palabras con timestamps para agrupar.</p>}
    </div>
  </section>
}
