import { describe, expect, it } from 'vitest'
import { normalizeTranscript } from './model'
import {
  groupTranscriptIntoPhrases,
  mergePhrases,
  phraseDuration,
  phraseWords,
  restorePhraseTranscription,
  splitPhrase,
  updatePhrase,
} from './phrases'

function transcriptFixture() {
  return normalizeTranscript({ language: 'es', segments: [{
    start: 0, end: 9, text: '', words: [
      { id: 'w1', text: 'Hola', start: 0, end: 0.3 },
      { id: 'w2', text: ' mundo,', start: 0.35, end: 0.7 },
      { id: 'w3', text: ' ¿cómo', start: 0.8, end: 1.1 },
      { id: 'w4', text: ' estás?', start: 1.2, end: 1.5 },
      { id: 'w5', text: ' bien', start: 2.3, end: 9 },
    ],
  }] })
}

describe('phrase grouping and editing', () => {
  it('groups by punctuation and a configurable significant pause without a five-second cap', () => {
    const transcript = transcriptFixture()
    const data = groupTranscriptIntoPhrases(transcript)
    expect(data.pauseThreshold).toBe(0.6)
    expect(data.phrases.map((phrase) => phrase.text)).toEqual(['Hola mundo,', '¿cómo estás?', 'bien'])
    expect(data.phrases.map((phrase) => phrase.id)).toEqual(['F001', 'F002', 'F003'])
    expect(data.phrases[2].end - data.phrases[2].start).toBeGreaterThan(5)
    expect(phraseDuration(data.phrases[2])).toBe(6.7)
  })

  it('uses configured pause threshold and leaves a short gap inside one phrase', () => {
    const transcript = normalizeTranscript({ segments: [{ start: 0, end: 2, text: '', words: [
      { id: 'a', text: 'Uno', start: 0, end: 0.2 },
      { id: 'b', text: ' dos', start: 0.85, end: 1 },
    ] }] })
    expect(groupTranscriptIntoPhrases(transcript, 0.6).phrases).toHaveLength(2)
    expect(groupTranscriptIntoPhrases(transcript, 0.7).phrases).toHaveLength(1)
  })

  it('separates word text when a source JSON token omits leading whitespace', () => {
    const transcript = normalizeTranscript({ segments: [{ start: 0, end: 2, text: '', words: [
      { id: 'x', text: 'Rusia', start: 0, end: 0.4 },
      { id: 'y', text: 'tiene', start: 0.5, end: 0.8 },
      { id: 'z', text: 'territorio.', start: 0.9, end: 1.4 },
    ] }] })
    expect(groupTranscriptIntoPhrases(transcript).phrases[0].text).toBe('Rusia tiene territorio.')
  })

  it('splits and merges phrases while retaining original word timestamps and references', () => {
    const transcript = transcriptFixture()
    const grouped = groupTranscriptIntoPhrases(transcript)
    const split = splitPhrase(grouped, 'F001', 1, transcript)
    expect(split.phrases.slice(0, 2).map((phrase) => phrase.id)).toEqual(['F001', 'F002'])
    expect(split.phrases[0].wordIds).toEqual(['w1'])
    expect(split.phrases[1].wordIds).toEqual(['w2'])
    const merged = mergePhrases(split, 'F001')
    expect(merged.phrases[0].text).toBe('Hola mundo,')
    expect(merged.phrases[0].wordIds).toEqual(['w1', 'w2'])
    expect(phraseWords(merged.phrases[0], transcript).map((word) => [word.start, word.end])).toEqual([[0, 0.3], [0.35, 0.7]])
    expect(transcript.segments[0].words[1].start).toBe(0.35)
  })

  it('edits phrase text and boundaries without changing the referenced words', () => {
    const transcript = transcriptFixture()
    const grouped = groupTranscriptIntoPhrases(transcript)
    const edited = updatePhrase(grouped, 'F001', { text: 'Texto corregido', start: 0.05, end: 0.8 })
    expect(edited.phrases[0]).toMatchObject({ id: 'F001', text: 'Texto corregido', start: 0.05, end: 0.8 })
    expect(edited.phrases[0].wordIds).toEqual(['w1', 'w2'])
    expect(transcript.segments[0].words[0].start).toBe(0)
    expect(updatePhrase(edited, 'F001', { end: 0 }).phrases[0].end).toBe(0.8)
  })

  it('restores persisted phrase IDs and rejects stale word references', () => {
    const transcript = transcriptFixture()
    const grouped = groupTranscriptIntoPhrases(transcript)
    expect(restorePhraseTranscription(grouped, transcript)?.phrases[1].id).toBe('F002')
    const stale = { ...grouped, phrases: [{ ...grouped.phrases[0], wordIds: ['missing'] }] }
    expect(restorePhraseTranscription(stale, transcript)).toBeNull()
  })
})
