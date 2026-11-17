import { memo, useEffect, useState } from 'react'
import type { Segment, Word } from './model'

interface Props {
  segments: Segment[]
  selectedSegmentId: string | null
  selectedWordId: string | null
  onSelectSegment: (id: string) => void
  onSelectWord: (segmentId: string, wordId: string) => void
  onUpdateWord: (segmentId: string, wordId: string, patch: Partial<Pick<Word, 'text' | 'start' | 'end'>>) => void
  onUpdateSegmentTimes: (segmentId: string, start: number, end: number) => void
  onUpdatePlainSegment: (segmentId: string, patch: { text?: string; start?: number; end?: number }) => void
  onSplit: (segmentId: string, wordIndex: number) => void
  onMerge: (segmentId: string) => void
  onDelete: (segmentId: string) => void
  onCreateFirstWord: (segmentId: string) => void
}

export const TranscriptEditor = memo(function TranscriptEditor(props: Props) {
  const { segments, selectedSegmentId, selectedWordId } = props
  return <section className="transcript-editor" aria-label="Editor de transcripción">
    <div className="section-heading"><div><p className="eyebrow">TEXTO Y TIEMPOS</p><h2>Transcripción</h2></div><span>{segments.length} segmentos</span></div>
    <div className="segment-list">
      {segments.map((segment, segmentIndex) => <article className={`segment-card${segment.id === selectedSegmentId ? ' segment-card--selected' : ''}`} key={segment.id}>
        <header className="segment-card-heading">
          <button className="segment-index" type="button" onClick={() => props.onSelectSegment(segment.id)}>SEG {String(segmentIndex + 1).padStart(2, '0')}</button>
          <label>Inicio <TimeInput value={segment.start} onCommit={(value) => updateBoundary(segment, 'start', value, props)} /></label>
          <label>Final <TimeInput value={segment.end} onCommit={(value) => updateBoundary(segment, 'end', value, props)} /></label>
          <div className="segment-actions">
            <button type="button" title="Unir con el segmento anterior" onClick={() => props.onMerge(segment.id)} disabled={segmentIndex === 0}>Unir anterior</button>
            <button type="button" title="Eliminar segmento" className="danger-button" onClick={() => props.onDelete(segment.id)}>Eliminar</button>
          </div>
        </header>
        {segment.words.length ? <div className="word-list">
          {segment.words.map((word, wordIndex) => <div className={`word-row${word.id === selectedWordId ? ' word-row--selected' : ''}`} key={word.id}>
            <button type="button" className="word-select" title="Localizar esta palabra" onClick={() => props.onSelectWord(segment.id, word.id)}>{String(wordIndex + 1).padStart(2, '0')}</button>
            <input aria-label={`Texto palabra ${wordIndex + 1}`} className="word-text" value={word.text} onFocus={() => props.onSelectWord(segment.id, word.id)} onChange={(event) => props.onUpdateWord(segment.id, word.id, { text: event.target.value })} />
            <TimeInput ariaLabel={`Inicio palabra ${wordIndex + 1}`} value={word.start} onCommit={(value) => props.onUpdateWord(segment.id, word.id, { start: value })} />
            <TimeInput ariaLabel={`Final palabra ${wordIndex + 1}`} value={word.end} onCommit={(value) => props.onUpdateWord(segment.id, word.id, { end: value })} />
            <button type="button" className="split-button" title="Dividir segmento antes de esta palabra" disabled={wordIndex === 0 || wordIndex === segment.words.length - 1} onClick={() => props.onSplit(segment.id, wordIndex)}>Dividir aquí</button>
          </div>)}
        </div> : <div className="plain-segment">
          <label>Texto del segmento<textarea value={segment.text} onFocus={() => props.onSelectSegment(segment.id)} onChange={(event) => props.onUpdatePlainSegment(segment.id, { text: event.target.value })} rows={2} /></label>
          <button type="button" onClick={() => props.onCreateFirstWord(segment.id)}>Crear palabra con este texto</button>
        </div>}
      </article>)}
      {segments.length === 0 && <p className="muted">La transcripción no tiene segmentos. Usa “Nuevo segmento” para comenzar.</p>}
    </div>
  </section>
})

function TimeInput({ value, onCommit, ariaLabel }: { value: number; onCommit: (value: number) => void; ariaLabel?: string }) {
  const [draft, setDraft] = useState(Number.isFinite(value) ? String(value) : '')
  const [focused, setFocused] = useState(false)
  useEffect(() => { if (!focused) setDraft(Number.isFinite(value) ? String(value) : '') }, [focused, value])
  return <input className="time-input" type="number" min="0" step="0.01" aria-label={ariaLabel} value={draft}
    onFocus={() => setFocused(true)}
    onChange={(event) => setDraft(event.target.value)}
    onBlur={() => {
      setFocused(false)
      const next = Number(draft)
      if (draft !== '' && Number.isFinite(next) && next >= 0) onCommit(next)
      else setDraft(Number.isFinite(value) ? String(value) : '')
    }}
    onKeyDown={(event) => { if (event.key === 'Enter') event.currentTarget.blur() }} />
}

function updateBoundary(segment: Segment, edge: 'start' | 'end', value: number, props: Props) {
  const start = edge === 'start' ? value : segment.start
  const end = edge === 'end' ? value : segment.end
  if (end <= start) return
  if (segment.words.length) props.onUpdateSegmentTimes(segment.id, start, end)
  else props.onUpdatePlainSegment(segment.id, { start, end })
}
