import { forwardRef, memo, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { updateProjectDocument, mediaUrl, type ProjectDocument } from '../api/client'
import { DesignPanel } from './DesignPanel'
import { ExportPanel } from './ExportPanel'
import { TranscriptionPanel } from './TranscriptionPanel'
import { buildAssLayout, type ExportLayout, type ExportLayoutDiagnostic } from './exportLayout'
import { composeCaptionLines } from './captionLayout'
import {
  dragNormalizedPosition,
  getContainedVideoRect,
  getSmartVideoGuides,
  snapCaptionAxisToGuides,
  type SmartGuide,
  type VideoContentRect,
} from './videoGeometry'
import { PlaybackClock } from './clock'
import { Timeline } from './Timeline'
import { TranscriptEditor } from './TranscriptEditor'
import {
  activeAt,
  createFirstWord,
  createSegment,
  designAtTime,
  formatSubtitleText,
  FONT_CHOICES,
  mergeWithPrevious,
  normalizeDesign,
  normalizedHorizontalOffset,
  normalizeTranscript,
  removeSegment,
  replacePlainSegment,
  replaceSegmentTimes,
  replaceWord,
  splitSegment,
  validateTranscript,
  type DesignProfile,
  type Segment,
  type Transcript,
  type Word,
} from './model'

export interface EditorWorkspaceHandle { flushPending: () => Promise<void> }
interface Props {
  project: ProjectDocument
  onStatus: (message: string) => void
  onTranscriptionProjectUpdated?: (project: ProjectDocument) => void
}

export const EditorWorkspace = forwardRef<EditorWorkspaceHandle, Props>(function EditorWorkspace({ project, onStatus, onTranscriptionProjectUpdated = () => undefined }, ref) {
  const documentData = project.document ?? {}
  const sourceTranscript = (documentData.transcription as Record<string, unknown> | undefined) ?? project.transcription
  const [transcript, setTranscript] = useState<Transcript | null>(() => {
    if (!sourceTranscript) return null
    try { return normalizeTranscript(sourceTranscript) } catch { return null }
  })
  const storedDesign = documentData.design as Partial<DesignProfile> | undefined
  const [design, setDesign] = useState<DesignProfile>(() => normalizeDesign(storedDesign))
  const [selectedSegmentId, setSelectedSegmentId] = useState<string | null>(null)
  const [selectedWordId, setSelectedWordId] = useState<string | null>(null)
  const [duration, setDuration] = useState(project.media?.duration ?? 0)
  const [videoAspect, setVideoAspect] = useState((project.media?.width && project.media?.height) ? project.media.width / project.media.height : 16 / 9)
  const [isPlaying, setIsPlaying] = useState(false)
  const [captionSelected, setCaptionSelected] = useState(false)
  const [snapEnabled, setSnapEnabled] = useState(true)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [viewport, setViewport] = useState({ width: window.innerWidth, height: window.innerHeight })
  const [videoContentRect, setVideoContentRect] = useState<VideoContentRect | null>(null)
  const [saveState, setSaveState] = useState<'saved' | 'pending' | 'saving' | 'error'>('saved')
  const [validationMessage, setValidationMessage] = useState('')
  const [editorError, setEditorError] = useState('')
  const videoRef = useRef<HTMLVideoElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const transcriptRef = useRef(transcript)
  const designRef = useRef(design)
  const revisionRef = useRef(0)
  const savedRevisionRef = useRef(0)
  const lastSaveErrorRef = useRef<Error | null>(null)
  const timerRef = useRef<number | null>(null)
  const saveChainRef = useRef<Promise<void>>(Promise.resolve())
  const clockRef = useRef<PlaybackClock | null>(null)
  if (!clockRef.current) clockRef.current = new PlaybackClock()
  const clock = clockRef.current
  const selectionRef = useRef<{ segmentId: string | null; wordId: string | null }>({ segmentId: null, wordId: null })

  const effectiveDuration = useMemo(() => Math.max(
    1,
    duration || 0,
    ...(transcript?.segments.map((segment) => segment.end) ?? []),
  ), [duration, transcript])
  const playbackDuration = duration > 0 ? duration : effectiveDuration

  const enqueueSave = useCallback((revision: number, nextTranscript: Transcript | null, nextDesign: DesignProfile) => {
    setSaveState('saving')
    saveChainRef.current = saveChainRef.current.catch(() => undefined).then(async () => {
      const nextDocument: Record<string, unknown> = { ...project.document, design: nextDesign }
      if (nextTranscript) nextDocument.transcription = nextTranscript
      await updateProjectDocument(project.id, nextDocument)
      savedRevisionRef.current = Math.max(savedRevisionRef.current, revision)
      lastSaveErrorRef.current = null
      if (revision === revisionRef.current) {
        setSaveState('saved')
        setValidationMessage('')
        onStatus('Cambios guardados en el documento del proyecto.')
      }
    }).catch((error: unknown) => {
      lastSaveErrorRef.current = error instanceof Error ? error : new Error('Error desconocido al guardar.')
      if (revision === revisionRef.current) {
        setSaveState('error')
        onStatus(error instanceof Error ? `Error al guardar: ${error.message}` : 'Error al guardar el proyecto.')
      }
    })
    return saveChainRef.current
  }, [onStatus, project.document, project.id])

  const scheduleSave = useCallback((nextTranscript: Transcript | null, nextDesign: DesignProfile) => {
    transcriptRef.current = nextTranscript
    designRef.current = nextDesign
    revisionRef.current += 1
    const revision = revisionRef.current
    if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    setSaveState('pending')
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null
      void enqueueSave(revision, nextTranscript, nextDesign)
    }, 550)
  }, [enqueueSave])

  const flushPending = useCallback(async () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
      await enqueueSave(revisionRef.current, transcriptRef.current, designRef.current)
    } else if (revisionRef.current > savedRevisionRef.current) {
      await enqueueSave(revisionRef.current, transcriptRef.current, designRef.current)
    } else {
      await saveChainRef.current
    }
    if (revisionRef.current > savedRevisionRef.current) throw lastSaveErrorRef.current ?? new Error('Los cambios siguen pendientes de guardado.')
  }, [enqueueSave])

  useImperativeHandle(ref, () => ({ flushPending }), [flushPending])

  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === 'hidden') {
        void flushPending().catch((error: unknown) => onStatus(error instanceof Error ? error.message : 'No se pudo guardar antes de ocultar el editor.'))
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility)
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
    }
  }, [flushPending, onStatus])

  useEffect(() => {
    const updateFullscreen = () => {
      setIsFullscreen(document.fullscreenElement === stageRef.current)
      setViewport({ width: window.innerWidth, height: window.innerHeight })
    }
    const updateViewport = () => setViewport({ width: window.innerWidth, height: window.innerHeight })
    document.addEventListener('fullscreenchange', updateFullscreen)
    window.addEventListener('resize', updateViewport)
    return () => {
      document.removeEventListener('fullscreenchange', updateFullscreen)
      window.removeEventListener('resize', updateViewport)
    }
  }, [])

  useLayoutEffect(() => {
    const stage = stageRef.current
    const video = videoRef.current
    if (!stage || !video) return

    const measureVideoWidth = () => {
      const stageRect = stage.getBoundingClientRect()
      const videoRect = video.getBoundingClientRect()
      const sourceWidth = video.videoWidth || project.media?.width || 0
      const sourceHeight = video.videoHeight || project.media?.height || 0
      const nextRect = getContainedVideoRect(stageRect, videoRect, sourceWidth, sourceHeight)
      if (!nextRect) return

      setVideoContentRect((current) => {
        if (current && Math.abs(current.left - nextRect.left) < 0.25
          && Math.abs(current.top - nextRect.top) < 0.25
          && Math.abs(current.width - nextRect.width) < 0.25
          && Math.abs(current.height - nextRect.height) < 0.25) return current
        return nextRect
      })
    }

    measureVideoWidth()
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(measureVideoWidth)
    observer?.observe(stage)
    observer?.observe(video)
    window.addEventListener('resize', measureVideoWidth)
    document.addEventListener('fullscreenchange', measureVideoWidth)

    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measureVideoWidth)
      document.removeEventListener('fullscreenchange', measureVideoWidth)
    }
  }, [project.id, project.media?.width, project.media?.height, videoAspect])

  useEffect(() => {
    const first = transcript?.segments[0]
    if (first && !selectionRef.current.segmentId) setSelection(first.id, first.words[0]?.id ?? null)
  }, [transcript])

  function setSelection(segmentId: string | null, wordId: string | null) {
    if (selectionRef.current.segmentId === segmentId && selectionRef.current.wordId === wordId) return
    selectionRef.current = { segmentId, wordId }
    setSelectedSegmentId(segmentId)
    setSelectedWordId(wordId)
  }

  function syncSelection(time: number, source = transcriptRef.current) {
    const active = source ? activeAt(source, time) : null
    setSelection(active?.segment.id ?? null, active?.word?.id ?? null)
  }

  function applyTranscript(next: Transcript, status = 'Edición aplicada.') {
    const ordered = { ...next, segments: [...next.segments].sort((left, right) => left.start - right.start) }
    const issue = validateTranscript(ordered)
    if (issue) {
      setValidationMessage(issue)
      return false
    }
    setValidationMessage('')
    setEditorError('')
    setTranscript(ordered)
    scheduleSave(ordered, designRef.current)
    syncSelection(clock.getSnapshot(), ordered)
    onStatus(status)
    return true
  }

  function applyDesign(next: DesignProfile) {
    setDesign(next)
    scheduleSave(transcriptRef.current, next)
  }

  function changePosition(axis: 'positionX' | 'positionY', value: number, atTime = clock.getSnapshot()) {
    const currentDesign = designRef.current
    const activeEvent = currentDesign.temporalEvents
      .filter((event) => atTime >= event.start && atTime < event.end)
      .sort((left, right) => right.start - left.start)[0]
    const coordinate = Math.min(100, Math.max(0, value))
    if (!activeEvent) {
      applyDesign({ ...currentDesign, [axis]: coordinate })
      return
    }
    applyDesign({
      ...currentDesign,
      temporalEvents: currentDesign.temporalEvents.map((event) => event.id === activeEvent.id
        ? { ...event, position: 'custom', [axis]: coordinate }
        : event),
    })
  }

  function seek(time: number) {
    const safeTime = Math.max(0, Math.min(playbackDuration, time))
    if (videoRef.current) videoRef.current.currentTime = safeTime
    clock.set(safeTime)
    syncSelection(safeTime)
  }

  function togglePlayback() {
    const video = videoRef.current
    if (!video) return
    if (video.paused) void video.play().catch((error: unknown) => onStatus(error instanceof Error ? error.message : 'No se pudo reproducir el video.'))
    else video.pause()
  }

  async function toggleFullscreen() {
    const stage = stageRef.current
    if (!stage) return
    try {
      if (document.fullscreenElement === stage) await document.exitFullscreen()
      else await stage.requestFullscreen()
    } catch (error) {
      onStatus(error instanceof Error ? `No se pudo cambiar a pantalla completa: ${error.message}` : 'El navegador no permitió pantalla completa.')
    }
  }

  function selectSegment(segmentId: string, seekToStart = false) {
    const segment = transcript?.segments.find((item) => item.id === segmentId)
    if (!segment) return
    setSelection(segmentId, segment.words[0]?.id ?? null)
    if (seekToStart) seek(segment.start)
  }

  function selectWord(segmentId: string, wordId: string) {
    const word = transcript?.segments.find((segment) => segment.id === segmentId)?.words.find((item) => item.id === wordId)
    setSelection(segmentId, wordId)
    if (word) seek(word.start)
  }

  function updateWord(segmentId: string, wordId: string, patch: Partial<Pick<Word, 'text' | 'start' | 'end'>>) {
    if (!transcript) return
    const word = transcript.segments.find((segment) => segment.id === segmentId)?.words.find((item) => item.id === wordId)
    if (!word) return
    const nextWord = { ...word, ...patch }
    if (nextWord.start < 0 || nextWord.end <= nextWord.start) {
      setValidationMessage('Cada palabra debe tener inicio no negativo y final posterior al inicio.')
      return
    }
    applyTranscript(replaceWord(transcript, segmentId, wordId, patch), 'Palabra actualizada.')
  }

  function updateSegmentTimes(segmentId: string, start: number, end: number) {
    if (!transcript) return
    if (start < 0 || end <= start) {
      setValidationMessage('El segmento debe tener inicio no negativo y final posterior al inicio.')
      return
    }
    applyTranscript(replaceSegmentTimes(transcript, segmentId, start, end), 'Tiempos de segmento actualizados.')
  }

  function updatePlainSegment(segmentId: string, patch: Partial<Pick<Segment, 'text' | 'start' | 'end'>>) {
    if (!transcript) return
    const current = transcript.segments.find((segment) => segment.id === segmentId)
    if (!current) return
    const next = { ...current, ...patch }
    if (next.start < 0 || next.end <= next.start) {
      setValidationMessage('El segmento debe tener inicio no negativo y final posterior al inicio.')
      return
    }
    applyTranscript(replacePlainSegment(transcript, segmentId, patch), 'Segmento actualizado.')
  }

  function doSplit(segmentId: string, wordIndex: number) {
    if (!transcript) return
    try {
      const result = splitSegment(transcript, segmentId, wordIndex)
      const sourceIndex = transcript.segments.findIndex((item) => item.id === segmentId)
      const right = result.segments[sourceIndex + 1]
      if (applyTranscript(result, 'Segmento dividido.')) setSelection(right?.id ?? segmentId, right?.words[0]?.id ?? null)
    } catch (error) { setEditorError(error instanceof Error ? error.message : 'No se pudo dividir el segmento.') }
  }

  function doMerge(segmentId: string) {
    if (!transcript) return
    try {
      const result = mergeWithPrevious(transcript, segmentId)
      const merged = result.segments.find((segment) => segment.id === segmentId) ?? result.segments[Math.max(0, transcript.segments.findIndex((item) => item.id === segmentId) - 1)]
      if (applyTranscript(result, 'Segmentos unidos.')) setSelection(merged?.id ?? null, merged?.words[0]?.id ?? null)
    } catch (error) { setEditorError(error instanceof Error ? error.message : 'No se pudieron unir los segmentos.') }
  }

  function doDelete(segmentId: string) {
    if (!transcript) return
    const index = transcript.segments.findIndex((segment) => segment.id === segmentId)
    const next = removeSegment(transcript, segmentId)
    if (applyTranscript(next, 'Segmento eliminado.')) {
      const fallback = next.segments[Math.min(index, next.segments.length - 1)]
      setSelection(fallback?.id ?? null, fallback?.words[0]?.id ?? null)
    }
  }

  function doCreateSegment() {
    if (!transcript) return
    try {
      const next = createSegment(transcript, clock.getSnapshot(), playbackDuration)
      const added = next.segments.find((segment) => !transcript.segments.some((item) => item.id === segment.id))
      if (applyTranscript(next, 'Nuevo segmento creado.')) {
        setSelection(added?.id ?? null, null)
      }
    } catch (error) { setEditorError(error instanceof Error ? error.message : 'No se pudo crear el segmento.') }
  }

  function doCreateFirstWord(segmentId: string) {
    if (!transcript) return
    const next = createFirstWord(transcript, segmentId)
    const word = next.segments.find((segment) => segment.id === segmentId)?.words[0]
    if (applyTranscript(next, 'Palabra creada.')) setSelection(segmentId, word?.id ?? null)
  }

  function navigateSegment(offset: number) {
    if (!transcript?.segments.length) return
    const current = transcript.segments.findIndex((segment) => segment.id === selectedSegmentId)
    const next = Math.max(0, Math.min(transcript.segments.length - 1, (current < 0 ? 0 : current) + offset))
    selectSegment(transcript.segments[next].id, true)
  }

  function navigateWord(offset: number) {
    const words = transcript?.segments.flatMap((segment) => segment.words.map((word) => ({ segmentId: segment.id, word }))) ?? []
    if (!words.length) return
    const current = words.findIndex((item) => item.word.id === selectedWordId)
    const next = Math.max(0, Math.min(words.length - 1, (current < 0 ? 0 : current) + offset))
    selectWord(words[next].segmentId, words[next].word.id)
  }

  function onTimeUpdate() {
    const time = videoRef.current?.currentTime ?? 0
    clock.set(time)
    syncSelection(time)
  }

  const fullscreenWidth = Math.max(1, Math.min(viewport.width, viewport.height * videoAspect))
  return <section className="editor-workspace">
    <div className="editor-toolbar">
      <div><p className="eyebrow">EDITOR VISUAL</p><h2>{project.name}</h2></div>
      <div className="toolbar-controls">
        {transcript && <>
          <button className="subtle-button" type="button" onClick={() => navigateSegment(-1)}>Segmento anterior</button>
          <button className="subtle-button" type="button" onClick={() => navigateSegment(1)}>Siguiente segmento</button>
          <button className="subtle-button" type="button" onClick={() => navigateWord(-1)}>Palabra anterior</button>
          <button className="subtle-button" type="button" onClick={() => navigateWord(1)}>Siguiente palabra</button>
          <button type="button" onClick={doCreateSegment}>Nuevo segmento</button>
        </>}
      </div>
      <span className={`save-indicator save-indicator--${saveState}`} role="status">{saveState === 'saved' ? 'Guardado' : saveState === 'pending' ? 'Pendiente…' : saveState === 'saving' ? 'Guardando…' : 'Error al guardar'}</span>
      {saveState === 'error' && <button className="subtle-button retry-save" type="button" onClick={() => void flushPending().catch((error: unknown) => onStatus(error instanceof Error ? error.message : 'No se pudo guardar.'))}>Reintentar guardado</button>}
    </div>

    <TranscriptionPanel
      projectId={project.id}
      hasMediaAudio={project.media?.has_audio === true}
      hasTranscript={Boolean(documentData.transcription ?? project.transcription ?? project.transcription_info)}
      flushPending={flushPending}
      onProjectUpdated={onTranscriptionProjectUpdated}
    />
    <ExportPanel
      projectId={project.id} hasMedia={Boolean(project.media)} hasTranscript={Boolean(transcript)}
      flushPending={flushPending}
      getAssLayout={(onDiagnostic) => transcript
        ? buildAssLayout(
          transcript, design, project.media?.width ?? 1080,
          stageRef.current?.clientWidth ?? project.media?.width ?? 1080,
          undefined, onDiagnostic,
        )
        : []}
      getAssDiagnostic={(layout: ExportLayout, diagnosticId: string, measured?: ExportLayoutDiagnostic) => {
        const first = layout[0]
        const sourceWidth = project.media?.width ?? 1080
        const stageWidth = stageRef.current?.clientWidth ?? sourceWidth
        const words = new Map((transcript?.segments.flatMap((segment) => segment.words) ?? []).map((word) => [word.id, word]))
        const lines = (first?.lines ?? []).map((wordIds, lineIndex) => {
          const lineWords = wordIds.map((wordId) => words.get(wordId)).filter((word): word is Word => Boolean(word))
          return {
            line: lineIndex + 1,
            word_ids: wordIds,
            source_text: lineWords.map((word) => word.text.trim()).join(' '),
            rendered_text: lineWords.map((word) => formatSubtitleText(word.text.trim(), design.textFormat)).join(' '),
            frontend_planner_measurement: measured?.lines[lineIndex] ?? null,
          }
        })
        return {
          diagnostic_id: diagnosticId,
          captured_at: new Date().toISOString(),
          project_id: project.id,
          video: { width: sourceWidth, height: project.media?.height ?? null, stage_width: stageWidth },
          design: {
            font_family: design.fontFamily,
            font_variant: design.fontFamily === 'Anton' || design.fontFamily === 'Bebas Neue' ? 'Regular' : design.fontFamily,
            css_weight: FONT_CHOICES.find((font) => font.label === design.fontFamily)?.weight ?? 400,
            configured_font_size_px: design.fontSize,
            max_width_percent: design.maxWidth,
            max_width_stage_px: stageWidth * design.maxWidth / 100,
            word_spacing_em: design.wordSpacing,
            letter_spacing_px: design.letterSpacing,
            outline_width_px: design.outlineWidth,
            shadow: design.shadow,
            position_x: design.positionX,
            position_y: design.positionY,
            text_format: design.textFormat,
          },
          cue1: {
            active_word_id: first?.active_word_id ?? null,
            frontend_manifest_font_size_px: first?.font_size ?? null,
            measured_layout: measured ?? null,
            lines,
          },
        }
      }}
    />

    {!project.media ? <div className="editor-empty">Importa un video para iniciar la reproducción y edición sincronizadas.</div> : <div className="preview-layout">
      <div className="preview-column">
        <div
          ref={stageRef}
          className="video-stage"
          onPointerDown={(event) => {
            if (!(event.target as HTMLElement).closest('.subtitle-preview, .playback-status')) setCaptionSelected(false)
          }}
          style={{
            aspectRatio: String(videoAspect),
            width: isFullscreen ? `${fullscreenWidth}px` : `min(100%, ${videoAspect * 72}vh)`,
            height: isFullscreen ? `${fullscreenWidth / videoAspect}px` : undefined,
            maxHeight: isFullscreen ? '100vh' : '72vh',
          }}
        >
          <button
            className="snap-toggle"
            type="button"
            aria-label={`Snap ${snapEnabled ? 'activado' : 'desactivado'}`}
            aria-pressed={snapEnabled}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => setSnapEnabled((enabled) => !enabled)}
          >Snap {snapEnabled ? 'activado' : 'desactivado'}</button>
          <video
            key={`${project.id}-${project.updated_at}`}
            aria-label="Reproductor de video"
            data-video-aspect={videoAspect}
            ref={videoRef}
            style={{ objectFit: 'contain' }}
            src={mediaUrl(project.id)}
            playsInline
            preload="metadata"
            onTimeUpdate={onTimeUpdate}
            onSeeking={onTimeUpdate}
            onSeeked={onTimeUpdate}
            onPlay={() => setIsPlaying(true)}
            onPause={() => { setIsPlaying(false); onTimeUpdate() }}
            onEnded={() => setIsPlaying(false)}
            onLoadedMetadata={(event) => {
              setDuration(event.currentTarget.duration || project.media?.duration || 1)
              if (event.currentTarget.videoWidth && event.currentTarget.videoHeight) setVideoAspect(event.currentTarget.videoWidth / event.currentTarget.videoHeight)
              clock.set(event.currentTarget.currentTime)
              syncSelection(event.currentTarget.currentTime)
            }}
          />
          <SubtitleOverlay
            transcript={transcript} clock={clock} design={design} stageRef={stageRef}
            layoutWidth={videoContentRect?.width ?? 0} videoContentRect={videoContentRect} selected={captionSelected}
            snapEnabled={snapEnabled}
            onSelect={() => setCaptionSelected(true)} onPositionChange={changePosition}
          />
          <PlaybackControls
            clock={clock}
            duration={playbackDuration}
            isPlaying={isPlaying}
            isFullscreen={isFullscreen}
            onSeek={seek}
            onTogglePlayback={togglePlayback}
            onToggleFullscreen={() => void toggleFullscreen()}
          />
        </div>
        {project.media.probe_error && <p className="warning">Metadatos no disponibles: {project.media.probe_error}</p>}
      </div>
      <div className="editor-side-panels">
        <DesignPanel value={design} onChange={applyDesign} clock={clock} onPositionChange={changePosition} />
        {transcript && <Timeline
          segments={transcript.segments}
          temporalEvents={design.temporalEvents}
          duration={effectiveDuration}
          clock={clock}
          selectedSegmentId={selectedSegmentId}
          selectedWordId={selectedWordId}
          onSeek={seek}
          onSelect={(segmentId, wordId) => setSelection(segmentId, wordId ?? null)}
        />}
      </div>
    </div>}

    {!transcript ? <div className="editor-empty">Este proyecto no tiene una transcripción válida. Importa un JSON con segmentos y tiempos para editarlo.</div> : <>
      <TranscriptEditor
        segments={transcript.segments}
        selectedSegmentId={selectedSegmentId}
        selectedWordId={selectedWordId}
        onSelectSegment={(id) => selectSegment(id, true)}
        onSelectWord={selectWord}
        onUpdateWord={updateWord}
        onUpdateSegmentTimes={updateSegmentTimes}
        onUpdatePlainSegment={updatePlainSegment}
        onSplit={doSplit}
        onMerge={doMerge}
        onDelete={doDelete}
        onCreateFirstWord={doCreateFirstWord}
      />
    </>}
    {(validationMessage || editorError) && <p className="editor-error" role="alert">{validationMessage || editorError}</p>}
  </section>
})

interface CaptionDragState {
  pointerId: number
  x: number
  y: number
  positionX: number
  positionY: number
  maxWidth: number
  startTime: number
  leftRatio: number
  topRatio: number
  widthRatio: number
  heightRatio: number
  guideX: string | null
  guideY: string | null
}

interface DragGuideDisplay {
  guideIds: string[]
  left: number
  right: number
  top: number
  bottom: number
}

const SubtitleOverlay = memo(function SubtitleOverlay({ transcript, clock, design, stageRef, layoutWidth, videoContentRect, selected, snapEnabled, onSelect, onPositionChange }: {
  transcript: Transcript | null
  clock: PlaybackClock
  design: DesignProfile
  stageRef: { current: HTMLDivElement | null }
  layoutWidth: number
  videoContentRect: VideoContentRect | null
  selected: boolean
  snapEnabled: boolean
  onSelect: () => void
  onPositionChange: (axis: 'positionX' | 'positionY', value: number, atTime?: number) => void
}) {
  const contentRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<CaptionDragState | null>(null)
  const [dragGuideDisplay, setDragGuideDisplay] = useState<DragGuideDisplay | null>(null)
  const currentTime = useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot)
  const caption = transcript ? activeAt(transcript, currentTime) : null
  if (!caption || (caption.segment.words.length > 0 && !caption.word)) return null
  const activeDesign = designAtTime(design, currentTime)
  const font = FONT_CHOICES.find((choice) => choice.label === activeDesign.fontFamily) ?? FONT_CHOICES[0]
  const stageWidth = layoutWidth || stageRef.current?.clientWidth || 1080
  const maxWidth = stageWidth * activeDesign.maxWidth / 100
  const initialFontSize = Math.max(10, stageWidth * activeDesign.fontSize / 1080)
  const canvas = document.createElement('canvas')
  const context = canvas.getContext('2d')
  const measureAt = (size: number, text: string) => {
    if (context) context.font = `${font.weight} ${size}px ${font.css}`
    return context?.measureText(text).width ?? text.length * size * 0.55
  }
  const { lines, fontSize } = composeCaptionLines(
    caption.segment.words,
    caption.word?.id,
    maxWidth,
    initialFontSize,
    measureAt,
    (size) => size * activeDesign.wordSpacing,
    (word) => formatSubtitleText(word.text.trim(), activeDesign.textFormat),
  )
  const smartGuides = getSmartVideoGuides()
  const updateGuideDisplay = (
    rect: { left: number; top: number; width: number; height: number },
    bounds: DOMRect,
    guideIds: string[],
  ) => setDragGuideDisplay({
    guideIds,
    left: Math.round(rect.left),
    right: Math.round(bounds.width - rect.left - rect.width),
    top: Math.round(rect.top),
    bottom: Math.round(bounds.height - rect.top - rect.height),
  })
  const contentStyle = videoContentRect
    ? {
      position: 'absolute' as const,
      left: `${videoContentRect.left}px`,
      top: `${videoContentRect.top}px`,
      width: `${videoContentRect.width}px`,
      height: `${videoContentRect.height}px`,
      pointerEvents: 'none' as const,
    }
    : { position: 'absolute' as const, inset: 0, pointerEvents: 'none' as const }
  return <div ref={contentRef} className="subtitle-video-content" style={contentStyle}>
    <div
    className={`subtitle-preview${selected ? ' subtitle-preview--selected' : ''}`}
    data-testid="subtitle-preview"
    aria-label="Bloque de subtítulos; clic para seleccionar y arrastrar para mover"
    onPointerDown={(event) => {
      event.preventDefault()
      event.stopPropagation()
      onSelect()
      const contentBounds = contentRef.current?.getBoundingClientRect()
      const stageBounds = stageRef.current?.getBoundingClientRect()
      const bounds = contentBounds?.width && contentBounds.height ? contentBounds : stageBounds
      const captionBounds = event.currentTarget.getBoundingClientRect()
      dragRef.current = {
        pointerId: event.pointerId, x: event.clientX, y: event.clientY,
        positionX: activeDesign.positionX, positionY: activeDesign.positionY,
        maxWidth: activeDesign.maxWidth, startTime: currentTime,
        leftRatio: bounds?.width ? (captionBounds.left - bounds.left) / bounds.width : 0,
        topRatio: bounds?.height ? (captionBounds.top - bounds.top) / bounds.height : 0,
        widthRatio: bounds?.width ? captionBounds.width / bounds.width : 0,
        heightRatio: bounds?.height ? captionBounds.height / bounds.height : 0,
        guideX: null,
        guideY: null,
      }
      if (bounds) {
        updateGuideDisplay({
          left: (captionBounds.left - bounds.left),
          top: (captionBounds.top - bounds.top),
          width: captionBounds.width,
          height: captionBounds.height,
        }, bounds, [])
      }
      event.currentTarget.setPointerCapture?.(event.pointerId)
    }}
    onPointerMove={(event) => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return
      const contentBounds = contentRef.current?.getBoundingClientRect()
      const stageBounds = stageRef.current?.getBoundingClientRect()
      const bounds = contentBounds?.width && contentBounds.height ? contentBounds : stageBounds
      if (!bounds) return
      const rawDeltaX = event.clientX - drag.x
      const rawDeltaY = event.clientY - drag.y
      const proposedRect = {
        left: drag.leftRatio * bounds.width + rawDeltaX,
        top: drag.topRatio * bounds.height + rawDeltaY,
        width: drag.widthRatio * bounds.width,
        height: drag.heightRatio * bounds.height,
      }
      const snapX = snapCaptionAxisToGuides(
        proposedRect.left, proposedRect.width, bounds.width, 'x',
        { enabled: snapEnabled, previousGuideId: drag.guideX },
      )
      const snapY = snapCaptionAxisToGuides(
        proposedRect.top, proposedRect.height, bounds.height, 'y',
        { enabled: snapEnabled, previousGuideId: drag.guideY },
      )
      drag.guideX = snapX.guideId
      drag.guideY = snapY.guideId
      const adjustedRect = {
        ...proposedRect,
        left: proposedRect.left + snapX.delta,
        top: proposedRect.top + snapY.delta,
      }
      updateGuideDisplay(adjustedRect, bounds, [...snapX.guideIds, ...snapY.guideIds])
      const next = dragNormalizedPosition(
        drag.positionX, drag.positionY, drag.maxWidth,
        rawDeltaX + snapX.delta, rawDeltaY + snapY.delta,
        bounds,
      )
      onPositionChange('positionX', next.positionX, drag.startTime)
      onPositionChange('positionY', next.positionY, drag.startTime)
    }}
    onPointerUp={(event) => {
      if (dragRef.current?.pointerId === event.pointerId) {
        dragRef.current = null
        setDragGuideDisplay(null)
      }
    }}
    onPointerCancel={() => { dragRef.current = null; setDragGuideDisplay(null) }}
    onLostPointerCapture={() => { dragRef.current = null; setDragGuideDisplay(null) }}
    style={{
      left: `${normalizedHorizontalOffset(activeDesign.positionX, activeDesign.maxWidth)}%`,
      top: `clamp(calc(4% + 1.08em), ${activeDesign.positionY}%, calc(96% - 1.08em))`,
      width: `${activeDesign.maxWidth}%`,
      color: activeDesign.textColor,
      fontFamily: font.css,
      fontWeight: font.weight,
      fontSize: `${fontSize}px`,
      letterSpacing: `${activeDesign.letterSpacing}px`,
      textAlign: activeDesign.alignment,
      WebkitTextStroke: `${activeDesign.outlineWidth}px ${activeDesign.outlineColor}`,
      textShadow: activeDesign.shadow ? `0 2px 5px ${activeDesign.outlineColor}` : 'none',
      ['--subtitle-word-gap' as string]: `${activeDesign.wordSpacing}em`,
      ['--subtitle-alignment' as string]: activeDesign.alignment === 'left' ? 'flex-start' : activeDesign.alignment === 'right' ? 'flex-end' : 'center',
    }}
  >
    {lines.length ? lines.map((line, lineIndex) => <span className="subtitle-line" key={`line-${lineIndex}`}>
      {line.map((word) => {
        const active = word.id === caption.word?.id
        return <span key={word.id} aria-current={active ? 'true' : undefined} className={active ? 'subtitle-word subtitle-word--active' : 'subtitle-word'} style={active ? { color: activeDesign.activeWordColor } : undefined}>{formatSubtitleText(word.text.trim(), activeDesign.textFormat)}</span>
      })}
    </span>) : formatSubtitleText(caption.segment.text, activeDesign.textFormat)}
    </div>
    {dragGuideDisplay && dragGuideDisplay.guideIds.length > 0 && <div className="subtitle-smart-guides" aria-hidden="true">
      {smartGuides.filter((guide: SmartGuide) => dragGuideDisplay.guideIds.includes(guide.id)).map((guide: SmartGuide) => <span
        key={guide.id}
        className={`subtitle-smart-guide subtitle-smart-guide--${guide.axis} subtitle-smart-guide--${guide.kind} is-active`}
        style={guide.axis === 'x' ? { left: `${guide.percent}%` } : { top: `${guide.percent}%` }}
      />)}
      <output className="subtitle-guide-distances">
        {dragGuideDisplay.guideIds.some((id) => id.startsWith('x-')) && <>Izq {dragGuideDisplay.left}px · Der {dragGuideDisplay.right}px</>}
        {dragGuideDisplay.guideIds.some((id) => id.startsWith('x-')) && dragGuideDisplay.guideIds.some((id) => id.startsWith('y-')) && ' · '}
        {dragGuideDisplay.guideIds.some((id) => id.startsWith('y-')) && <>Sup {dragGuideDisplay.top}px · Inf {dragGuideDisplay.bottom}px</>}
      </output>
    </div>}
  </div>
})

const PlaybackControls = memo(function PlaybackControls({ clock, duration, isPlaying, isFullscreen, onSeek, onTogglePlayback, onToggleFullscreen }: {
  clock: PlaybackClock
  duration: number
  isPlaying: boolean
  isFullscreen: boolean
  onSeek: (time: number) => void
  onTogglePlayback: () => void
  onToggleFullscreen: () => void
}) {
  const currentTime = useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot)
  return <div className="playback-status">
    <button className="playback-toggle" type="button" aria-label={isPlaying ? 'Pausar video' : 'Reproducir video'} onClick={onTogglePlayback}>{isPlaying ? '❚❚' : '▶'}</button>
    <button className="fullscreen-toggle" type="button" aria-label={isFullscreen ? 'Salir de pantalla completa' : 'Entrar en pantalla completa'} onClick={onToggleFullscreen}>⛶</button>
    <span>{currentTime.toFixed(2)} s</span><span>{duration.toFixed(2)} s</span>
    <input aria-label="Posición del video" type="range" min="0" max={duration} step="0.01" value={Math.min(currentTime, duration)} onChange={(event) => onSeek(Number(event.target.value))} />
  </div>
})
