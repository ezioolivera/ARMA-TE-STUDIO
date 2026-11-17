export interface Word {
  id: string
  text: string
  start: number
  end: number
  probability?: number
  [key: string]: unknown
}

export interface Segment {
  id: string
  text: string
  start: number
  end: number
  words: Word[]
  [key: string]: unknown
}

export interface Transcript {
  language?: string
  language_probability?: number
  segments: Segment[]
  [key: string]: unknown
}

export type TextFormat = 'original' | 'uppercase' | 'lowercase' | 'title'
export type TemporalPosition = 'base' | 'top' | 'center' | 'bottom' | 'custom'
export type TemporalStyleOverrides = Pick<DesignProfile,
  'fontFamily' | 'fontSize' | 'textColor' | 'activeWordColor' | 'outlineColor' |
  'outlineWidth' | 'shadow' | 'alignment' | 'letterSpacing' | 'wordSpacing' | 'maxWidth'
>

export interface TemporalDesignEvent {
  id: string
  start: number
  end: number
  position: TemporalPosition
  positionX: number
  positionY: number
  /** Future temporal style controls can store overrides without changing the base profile. */
  overrides?: Partial<TemporalStyleOverrides>
}

export interface DesignProfile {
  fontFamily: string
  fontSize: number
  textColor: string
  activeWordColor: string
  outlineColor: string
  outlineWidth: number
  shadow: boolean
  alignment: 'left' | 'center' | 'right'
  positionX: number
  positionY: number
  letterSpacing: number
  wordSpacing: number
  maxWidth: number
  textFormat: TextFormat
  temporalEvents: TemporalDesignEvent[]
}

export const DEFAULT_DESIGN: DesignProfile = {
  fontFamily: 'Montserrat ExtraBold',
  fontSize: 56,
  textColor: '#ffffff',
  activeWordColor: '#37d7d5',
  outlineColor: '#000000',
  outlineWidth: 2,
  shadow: true,
  alignment: 'center',
  positionX: 50,
  positionY: 62,
  letterSpacing: 0,
  wordSpacing: 0.16,
  maxWidth: 85,
  textFormat: 'original',
  temporalEvents: [],
}

export function normalizeDesign(value: unknown): DesignProfile {
  const source = value && typeof value === 'object' ? value as Partial<DesignProfile> : {}
  const fontFamily = FONT_CHOICES.some((font) => font.label === source.fontFamily) ? source.fontFamily! : DEFAULT_DESIGN.fontFamily
  const alignment = source.alignment === 'left' || source.alignment === 'right' ? source.alignment : 'center'
  const textFormat: TextFormat = source.textFormat === 'uppercase' || source.textFormat === 'lowercase' || source.textFormat === 'title'
    ? source.textFormat : 'original'
  const color = (candidate: unknown, fallback: string) => typeof candidate === 'string' && /^#[0-9a-f]{6}$/i.test(candidate) ? candidate : fallback
  const numeric = (candidate: unknown, fallback: number, min: number, max: number) => {
    const parsed = Number(candidate)
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback
  }
  return {
    fontFamily,
    fontSize: numeric(source.fontSize, DEFAULT_DESIGN.fontSize, 18, 96),
    textColor: color(source.textColor, DEFAULT_DESIGN.textColor),
    activeWordColor: color(source.activeWordColor, DEFAULT_DESIGN.activeWordColor),
    outlineColor: color(source.outlineColor, DEFAULT_DESIGN.outlineColor),
    outlineWidth: numeric(source.outlineWidth, DEFAULT_DESIGN.outlineWidth, 0, 8),
    shadow: typeof source.shadow === 'boolean' ? source.shadow : DEFAULT_DESIGN.shadow,
    alignment,
    positionX: numeric(source.positionX, DEFAULT_DESIGN.positionX, 0, 100),
    positionY: numeric(source.positionY, DEFAULT_DESIGN.positionY, 0, 100),
    letterSpacing: numeric(source.letterSpacing, DEFAULT_DESIGN.letterSpacing, -2, 8),
    wordSpacing: numeric(source.wordSpacing, DEFAULT_DESIGN.wordSpacing, 0, 1),
    maxWidth: numeric(source.maxWidth, DEFAULT_DESIGN.maxWidth, 30, 100),
    textFormat,
    temporalEvents: normalizeTemporalEvents(source.temporalEvents),
  }
}

function normalizeTemporalEvents(value: unknown): TemporalDesignEvent[] {
  if (!Array.isArray(value)) return []
  const seen = new Set<string>()
  return value.flatMap((item): TemporalDesignEvent[] => {
    if (!item || typeof item !== 'object') return []
    const source = item as Partial<TemporalDesignEvent>
    const id = typeof source.id === 'string' && source.id ? source.id : newId('position')
    const start = Number(source.start)
    const end = Number(source.end)
    const positions: TemporalPosition[] = ['base', 'top', 'center', 'bottom', 'custom']
    const position = positions.includes(source.position as TemporalPosition) ? source.position as TemporalPosition : 'top'
    if (seen.has(id) || !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return []
    seen.add(id)
    const numeric = (candidate: unknown, fallback: number) => {
      const parsed = Number(candidate)
      return Number.isFinite(parsed) ? Math.min(100, Math.max(0, parsed)) : fallback
    }
    const event: TemporalDesignEvent = {
      id, start, end, position,
      positionX: numeric(source.positionX, DEFAULT_DESIGN.positionX),
      positionY: numeric(source.positionY, position === 'top' ? 20 : position === 'center' ? 50 : DEFAULT_DESIGN.positionY),
    }
    if (source.overrides && typeof source.overrides === 'object') event.overrides = source.overrides
    return [event]
  }).sort((left, right) => left.start - right.start || left.end - right.end)
}

export function designAtTime(design: DesignProfile, time: number): DesignProfile {
  const event = design.temporalEvents.filter((item) => time >= item.start && time < item.end)
    .sort((left, right) => right.start - left.start)[0]
  if (!event) return design
  const presetY = event.position === 'top' ? 20
    : event.position === 'center' ? 50
      : event.position === 'bottom' ? 82
        : event.position === 'base' ? design.positionY
          : event.positionY
  const positionX = event.position === 'base' ? design.positionX : event.positionX
  return {
    ...design,
    ...event.overrides,
    positionX,
    positionY: presetY,
  }
}

export function formatSubtitleText(text: string, format: TextFormat): string {
  if (format === 'uppercase') return text.toLocaleUpperCase('es')
  if (format === 'lowercase') return text.toLocaleLowerCase('es')
  if (format === 'title') {
    return text.toLocaleLowerCase('es').replace(/\p{L}[\p{L}\p{M}'’-]*/gu, (word) =>
      word.charAt(0).toLocaleUpperCase('es') + word.slice(1))
  }
  return text
}

/** Map a 0–100 slider onto the usable left-to-right range for the caption width. */
export function normalizedHorizontalOffset(positionX: number, maxWidth: number): number {
  const position = Math.min(100, Math.max(0, Number.isFinite(positionX) ? positionX : DEFAULT_DESIGN.positionX))
  const width = Math.min(100, Math.max(0, Number.isFinite(maxWidth) ? maxWidth : DEFAULT_DESIGN.maxWidth))
  return position * (100 - width) / 100
}

export const FONT_CHOICES = [
  { label: 'Montserrat ExtraBold', css: '"Montserrat Variable", Arial, sans-serif', weight: 800 },
  { label: 'Montserrat Black', css: '"Montserrat Variable", Arial, sans-serif', weight: 900 },
  { label: 'Anton', css: 'Anton, Impact, sans-serif', weight: 400 },
  { label: 'Bebas Neue', css: '"Bebas Neue", Impact, sans-serif', weight: 400 },
  { label: 'Arial Bold', css: 'Arial, sans-serif', weight: 700 },
] as const

function stableId(prefix: string, index: number): string {
  return `${prefix}-${index + 1}`
}

export function normalizeTranscript(value: unknown): Transcript {
  if (!value || typeof value !== 'object' || !Array.isArray((value as Transcript).segments)) {
    throw new Error('La transcripción no contiene una lista válida de segmentos.')
  }
  const source = value as Record<string, unknown>
  const segments = (source.segments as unknown[]).map((raw, segmentIndex): Segment => {
    if (!raw || typeof raw !== 'object') throw new Error(`El segmento ${segmentIndex + 1} no es válido.`)
    const segment = raw as Record<string, unknown>
    const words = Array.isArray(segment.words) ? segment.words.map((rawWord, wordIndex): Word => {
      if (!rawWord || typeof rawWord !== 'object') throw new Error(`La palabra ${wordIndex + 1} no es válida.`)
      const word = rawWord as Record<string, unknown>
      return {
        ...word,
        id: String(word.id ?? stableId(`s${segmentIndex + 1}-w`, wordIndex)),
        text: typeof word.text === 'string' ? word.text : '',
        start: finiteNumber(word.start, `inicio de palabra ${wordIndex + 1}`),
        end: finiteNumber(word.end, `final de palabra ${wordIndex + 1}`),
      }
    }) : []
    const start = finiteNumber(segment.start, `inicio de segmento ${segmentIndex + 1}`)
    const end = finiteNumber(segment.end, `final de segmento ${segmentIndex + 1}`)
    if (start < 0 || end <= start) throw new Error(`El intervalo del segmento ${segmentIndex + 1} no es válido.`)
    words.forEach((word, wordIndex) => {
      if (word.start < 0 || word.end <= word.start) throw new Error(`El intervalo de la palabra ${wordIndex + 1} no es válido.`)
    })
    return {
      ...segment,
      id: String(segment.id ?? stableId('segment', segmentIndex)),
      start,
      end,
      words,
      text: typeof segment.text === 'string' ? segment.text : words.map((word) => word.text).join(''),
    }
  })
  return { ...source, segments } as Transcript
}

function finiteNumber(value: unknown, label: string): number {
  const number = Number(value)
  if (!Number.isFinite(number)) throw new Error(`Tiempo inválido: ${label}.`)
  return number
}

export function activeAt(transcript: Transcript, time: number): { segment: Segment; word: Word | null } | null {
  const segment = transcript.segments.find((item) => time >= item.start && time < item.end)
  if (!segment) return null
  const word = segment.words.find((item) => time >= item.start && time < item.end) ?? null
  return { segment, word }
}

export function segmentText(words: Word[]): string {
  return words.map((word) => word.text).join('')
}

export function replaceWord(transcript: Transcript, segmentId: string, wordId: string, patch: Partial<Pick<Word, 'text' | 'start' | 'end'>>): Transcript {
  return {
    ...transcript,
    segments: transcript.segments.map((segment) => {
      if (segment.id !== segmentId) return segment
      const words = segment.words.map((word) => word.id === wordId ? { ...word, ...patch } : word)
      const timedWords = words.filter((word) => Number.isFinite(word.start) && Number.isFinite(word.end))
      return {
        ...segment,
        words,
        text: segmentText(words),
        start: timedWords.length ? Math.min(...timedWords.map((word) => word.start)) : segment.start,
        end: timedWords.length ? Math.max(...timedWords.map((word) => word.end)) : segment.end,
      }
    }),
  }
}

export function replacePlainSegment(transcript: Transcript, segmentId: string, patch: Partial<Pick<Segment, 'text' | 'start' | 'end'>>): Transcript {
  return {
    ...transcript,
    segments: transcript.segments.map((segment) => segment.id === segmentId ? { ...segment, ...patch } : segment),
  }
}

export function replaceSegmentTimes(transcript: Transcript, segmentId: string, start: number, end: number): Transcript {
  return {
    ...transcript,
    segments: transcript.segments.map((segment) => {
      if (segment.id !== segmentId) return segment
      const words = [...segment.words]
      if (words.length) {
        words[0] = { ...words[0], start }
        words[words.length - 1] = { ...words[words.length - 1], end }
      }
      return { ...segment, start, end, words }
    }),
  }
}

export function createFirstWord(transcript: Transcript, segmentId: string): Transcript {
  return {
    ...transcript,
    segments: transcript.segments.map((segment) => segment.id === segmentId
      ? { ...segment, words: [{ id: newId('word'), text: segment.text, start: segment.start, end: segment.end }] }
      : segment),
  }
}

export function validateTranscript(transcript: Transcript): string | null {
  const segmentIds = new Set<string>()
  const wordIds = new Set<string>()
  for (const [segmentIndex, segment] of transcript.segments.entries()) {
    if (segmentIds.has(segment.id)) return `El identificador del segmento ${segmentIndex + 1} está duplicado.`
    segmentIds.add(segment.id)
    if (!Number.isFinite(segment.start) || !Number.isFinite(segment.end) || segment.start < 0 || segment.end <= segment.start) {
      return `El intervalo del segmento ${segmentIndex + 1} no es válido.`
    }
    for (const [wordIndex, word] of segment.words.entries()) {
      if (wordIds.has(word.id)) return `El identificador de la palabra ${wordIndex + 1} está duplicado.`
      wordIds.add(word.id)
      if (!Number.isFinite(word.start) || !Number.isFinite(word.end) || word.start < 0 || word.end <= word.start) {
        return `El intervalo de la palabra ${wordIndex + 1} no es válido.`
      }
      if (word.start < segment.start - 0.001 || word.end > segment.end + 0.001) {
        return `La palabra ${wordIndex + 1} queda fuera de los tiempos de su segmento.`
      }
      if (wordIndex > 0 && word.start < segment.words[wordIndex - 1].start) {
        return `Las palabras del segmento ${segmentIndex + 1} deben conservar su orden temporal.`
      }
      if (typeof word.text !== 'string') return `El texto de la palabra ${wordIndex + 1} no es válido.`
    }
  }
  return null
}

export function splitSegment(transcript: Transcript, segmentId: string, wordIndex: number): Transcript {
  const index = transcript.segments.findIndex((segment) => segment.id === segmentId)
  if (index < 0) throw new Error('No se encontró el segmento seleccionado.')
  const source = transcript.segments[index]
  if (wordIndex <= 0 || wordIndex >= source.words.length) throw new Error('Selecciona una palabra interna para dividir el segmento.')
  const leftWords = source.words.slice(0, wordIndex)
  const rightWords = source.words.slice(wordIndex)
  const left = makeSegment(source, leftWords, source.id)
  const right = makeSegment(source, rightWords, newId('segment'))
  const segments = [...transcript.segments]
  segments.splice(index, 1, left, right)
  return { ...transcript, segments }
}

export function mergeWithPrevious(transcript: Transcript, segmentId: string): Transcript {
  const index = transcript.segments.findIndex((segment) => segment.id === segmentId)
  if (index <= 0) throw new Error('Este segmento no tiene un segmento anterior para unir.')
  const previous = transcript.segments[index - 1]
  const current = transcript.segments[index]
  const words = [...previous.words, ...current.words]
  const merged = makeSegment({ ...previous, text: `${previous.text}${current.text}` }, words, previous.id)
  const segments = [...transcript.segments]
  segments.splice(index - 1, 2, merged)
  return { ...transcript, segments }
}

export function createSegment(transcript: Transcript, time: number, duration: number): Transcript {
  const safeDuration = duration > 0 ? duration : Math.max(1, time + 1)
  const start = Math.max(0, Math.min(time, Math.max(0, safeDuration - 0.05)))
  const end = Math.min(safeDuration, start + Math.max(0.05, Math.min(1, safeDuration - start)))
  if (end <= start) throw new Error('No hay espacio de tiempo para crear un segmento.')
  const segment: Segment = { id: newId('segment'), start, end, text: '', words: [] }
  const index = transcript.segments.findIndex((item) => item.start > start)
  const segments = [...transcript.segments]
  segments.splice(index < 0 ? segments.length : index, 0, segment)
  return { ...transcript, segments }
}

export function removeSegment(transcript: Transcript, segmentId: string): Transcript {
  return { ...transcript, segments: transcript.segments.filter((segment) => segment.id !== segmentId) }
}

function makeSegment(source: Segment, words: Word[], id: string): Segment {
  if (!words.length) return { ...source, id }
  return {
    ...source,
    id,
    start: Math.min(...words.map((word) => word.start)),
    end: Math.max(...words.map((word) => word.end)),
    words,
    text: segmentText(words),
  }
}

function newId(prefix: string): string {
  return `${prefix}-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`
}
