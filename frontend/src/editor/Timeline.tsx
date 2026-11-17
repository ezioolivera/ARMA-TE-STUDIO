import { memo, useRef, useState, useSyncExternalStore, type PointerEvent } from 'react'
import type { PlaybackClock } from './clock'
import type { Segment, TemporalDesignEvent } from './model'

interface Props {
  segments: Segment[]
  temporalEvents: TemporalDesignEvent[]
  duration: number
  clock: PlaybackClock
  selectedSegmentId: string | null
  selectedWordId: string | null
  onSeek: (time: number) => void
  onSelect: (segmentId: string, wordId?: string) => void
}

export const Timeline = memo(function Timeline({ segments, temporalEvents, duration, clock, selectedSegmentId, selectedWordId, onSeek, onSelect }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [zoom, setZoom] = useState(18)
  const [dragging, setDragging] = useState(false)
  const pixelsPerSecond = zoom
  const trackWidth = Math.max(720, Math.ceil(duration * pixelsPerSecond))

  function seekFromPointer(event: PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect()
    const x = event.clientX - bounds.left
    onSeek(Math.max(0, Math.min(duration, x / pixelsPerSecond)))
  }

  function onTrackPointerDown(event: PointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest('[data-timeline-item]')) return
    try { event.currentTarget.setPointerCapture(event.pointerId) } catch { /* Pointer capture is not available in every test/browser host. */ }
    setDragging(true)
    seekFromPointer(event)
  }

  function onTrackPointerMove(event: PointerEvent<HTMLDivElement>) {
    if (dragging) seekFromPointer(event)
  }

  const rulerStep = duration > 180 ? 10 : duration > 60 ? 5 : 2
  const rulerMarks = Array.from({ length: Math.floor(duration / rulerStep) + 1 }, (_, index) => index * rulerStep)

  return <section className="timeline-panel" aria-label="Timeline de subtítulos">
    <div className="timeline-heading"><h3>Timeline</h3><label>Zoom <input aria-label="Zoom de timeline" type="range" min="8" max="60" value={zoom} onChange={(event) => setZoom(Number(event.target.value))} /></label><span>{duration.toFixed(1)} s</span></div>
    <div className="timeline-scroll" ref={scrollRef}>
      <div className="timeline-canvas" style={{ width: `${trackWidth}px` }}>
        <div className="timeline-ruler" style={{ width: `${trackWidth}px` }}>
          {rulerMarks.map((time) => <span key={time} style={{ left: `${time * pixelsPerSecond}px` }}>{formatTime(time)}</span>)}
        </div>
        <div
          className="timeline-track"
          style={{ width: `${trackWidth}px`, backgroundSize: `${pixelsPerSecond}px 100%` }}
          onPointerDown={onTrackPointerDown}
          onPointerMove={onTrackPointerMove}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
        >
          {segments.map((segment, segmentIndex) => {
            const left = segment.start * pixelsPerSecond
            const width = Math.max(8, (segment.end - segment.start) * pixelsPerSecond)
            return <div
              className={`timeline-segment${segment.id === selectedSegmentId ? ' timeline-segment--selected' : ''}`}
              key={segment.id}
              data-timeline-item
              role="group"
              aria-label={`Segmento ${segmentIndex + 1}, ${formatTime(segment.start)} a ${formatTime(segment.end)}`}
              style={{ left: `${left}px`, width: `${width}px` }}
              onClick={() => { onSelect(segment.id); onSeek(segment.start) }}
            >
              <span className="timeline-segment-label">{segment.text || `Segmento ${segmentIndex + 1}`}</span>
              {segment.words.map((word) => <button
                type="button"
                title={`${word.text} · ${word.start.toFixed(2)}–${word.end.toFixed(2)} s`}
                aria-label={`Palabra ${word.text || 'vacía'} a ${word.start.toFixed(2)} segundos`}
                className={`timeline-word${word.id === selectedWordId ? ' timeline-word--selected' : ''}`}
                key={word.id}
                style={{ left: `${(word.start - segment.start) * pixelsPerSecond}px`, width: `${Math.max(3, (word.end - word.start) * pixelsPerSecond)}px` }}
                onClick={(event) => { event.stopPropagation(); onSelect(segment.id, word.id); onSeek(word.start) }}
              />)}
            </div>
          })}
          {temporalEvents.map((event) => <button
            type="button"
            className={`timeline-position-event timeline-position-event--${event.position}`}
            key={event.id}
            data-timeline-item
            title={`Posición ${event.position}: ${formatTime(event.start)}–${formatTime(event.end)}`}
            aria-label={`Cambio temporal ${event.position}, ${formatTime(event.start)} a ${formatTime(event.end)}`}
            style={{ left: `${event.start * pixelsPerSecond}px`, width: `${Math.max(8, (event.end - event.start) * pixelsPerSecond)}px` }}
            onClick={(pointer) => { pointer.stopPropagation(); onSeek(event.start) }}
          />)}
          <TimelinePlayhead clock={clock} pixelsPerSecond={pixelsPerSecond} />
        </div>
      </div>
    </div>
    <p className="timeline-hint">Arrastra el cabezal o selecciona un punto vacío para buscar en el video. Desplázate horizontalmente para recorrer el tiempo.</p>
  </section>
})

function TimelinePlayhead({ clock, pixelsPerSecond }: { clock: PlaybackClock; pixelsPerSecond: number }) {
  const currentTime = useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot)
  return <div className="timeline-playhead" style={{ left: `${currentTime * pixelsPerSecond}px` }} aria-label={`Cabezal ${formatTime(currentTime)}`} />
}

function formatTime(time: number): string {
  const minutes = Math.floor(time / 60)
  const seconds = Math.floor(time % 60)
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}
