import { describe, expect, it } from 'vitest'
import {
  activeAt,
  designAtTime,
  createSegment,
  DEFAULT_DESIGN,
  formatSubtitleText,
  mergeWithPrevious,
  normalizeDesign,
  normalizedHorizontalOffset,
  normalizeTranscript,
  removeSegment,
  replaceWord,
  splitSegment,
  validateTranscript,
} from './model'
import type { Transcript } from './model'

function fixture(): Transcript {
  return normalizeTranscript({
    language: 'es',
    segments: [{
      start: 0,
      end: 1.2,
      text: 'Hola mundo hoy',
      words: [
        { text: 'Hola', start: 0, end: 0.4 },
        { text: ' mundo', start: 0.4, end: 0.9 },
        { text: ' hoy', start: 0.9, end: 1.2 },
      ],
    }],
  })
}

describe('transcript editor model', () => {
  it('edits word text and timestamps while retaining stable IDs', () => {
    const original = fixture()
    const segment = original.segments[0]
    const word = segment.words[1]
    const changed = replaceWord(original, segment.id, word.id, { text: ' planeta', start: 0.42, end: 0.88 })
    expect(changed.segments[0].words[1]).toMatchObject({ id: word.id, text: ' planeta', start: 0.42, end: 0.88 })
    expect(changed.segments[0].id).toBe(segment.id)
    expect(changed.segments[0].text).toBe('Hola planeta hoy')
  })

  it('splits and joins segments without replacing existing word identifiers', () => {
    const original = fixture()
    const [first] = original.segments
    const originalWordIds = first.words.map((word) => word.id)
    const divided = splitSegment(original, first.id, 1)
    expect(divided.segments).toHaveLength(2)
    expect(divided.segments[0].id).toBe(first.id)
    expect(divided.segments[0].words.map((word) => word.id)).toEqual(originalWordIds.slice(0, 1))
    expect(divided.segments[1].words.map((word) => word.id)).toEqual(originalWordIds.slice(1))

    const joined = mergeWithPrevious(divided, divided.segments[1].id)
    expect(joined.segments).toHaveLength(1)
    expect(joined.segments[0].id).toBe(first.id)
    expect(joined.segments[0].words.map((word) => word.id)).toEqual(originalWordIds)
  })

  it('creates and deletes segments with new then preserved stable IDs', () => {
    const original = fixture()
    const created = createSegment(original, 2.1, 10)
    expect(created.segments).toHaveLength(2)
    const newSegment = created.segments.find((segment) => !original.segments.some((item) => item.id === segment.id))!
    expect(newSegment.id).toBeTruthy()
    expect(removeSegment(created, newSegment.id).segments).toEqual(original.segments)
  })

  it('selects only the segment and word active in the half-open timestamp interval', () => {
    const transcript = fixture()
    const segment = transcript.segments[0]
    expect(activeAt(transcript, -0.01)).toBeNull()
    expect(activeAt(transcript, 0)?.word?.id).toBe(segment.words[0].id)
    expect(activeAt(transcript, 0.4)?.word?.id).toBe(segment.words[1].id)
    expect(activeAt(transcript, 1.2)).toBeNull()
  })

  it('rejects malformed transcript structures and invalid word/segment durations', () => {
    expect(() => normalizeTranscript({ language: 'es' })).toThrow(/segmentos/i)
    expect(() => normalizeTranscript({ segments: [{ start: -1, end: 1, text: 'x', words: [] }] })).toThrow(/segmento/i)
    expect(() => normalizeTranscript({ segments: [{ start: 0, end: 1, text: 'x', words: [{ text: 'x', start: 0.5, end: 0.5 }] }] })).toThrow(/palabra/i)
    const invalid = fixture()
    invalid.segments[0].words[0].end = 0
    expect(validateTranscript(invalid)).toMatch(/intervalo/i)
  })

  it('normalizes design controls and clamps saved positions to the supported range', () => {
    expect(normalizeDesign({ positionX: 140, positionY: -20, maxWidth: 10, fontSize: 150 })).toMatchObject({
      positionX: 100,
      positionY: 0,
      maxWidth: 30,
      fontSize: 96,
    })
    expect(normalizeDesign({ positionX: 'bad', textColor: 'turquoise', alignment: 'justify' })).toMatchObject({
      positionX: DEFAULT_DESIGN.positionX,
      textColor: DEFAULT_DESIGN.textColor,
      alignment: 'center',
    })
    expect(normalizedHorizontalOffset(80, 85)).toBe(12)
    expect(normalizedHorizontalOffset(50, 100)).toBe(0)
  })

  it('resolves half-open temporary position events and returns to the base profile', () => {
    const base = normalizeDesign({ positionX: 45, positionY: 62, temporalEvents: [{
      id: 'top-30', start: 30, end: 36, position: 'top', positionX: 50, positionY: 20,
    }] })
    expect(designAtTime(base, 29.99).positionY).toBe(62)
    expect(designAtTime(base, 30).positionY).toBe(20)
    expect(designAtTime(base, 35.999).positionY).toBe(20)
    expect(designAtTime(base, 36)).toBe(base)
    expect(designAtTime(base, 32).positionX).toBe(50)
    expect(designAtTime(base, 37).positionX).toBe(45)
    expect(normalizeDesign({}).temporalEvents).toEqual([])
  })

  it('formats Spanish subtitle words without changing their source text', () => {
    const original = '¿qué pasó con ñandú?'
    expect(formatSubtitleText(original, 'uppercase')).toBe('¿QUÉ PASÓ CON ÑANDÚ?')
    expect(formatSubtitleText(original, 'lowercase')).toBe(original)
    expect(formatSubtitleText(original, 'title')).toBe('¿Qué Pasó Con Ñandú?')
    expect(formatSubtitleText(original, 'original')).toBe(original)
    expect(original).toBe('¿qué pasó con ñandú?')
  })
})
