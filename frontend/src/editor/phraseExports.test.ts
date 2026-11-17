import { describe, expect, it } from 'vitest'
import { normalizeTranscript } from './model'
import { groupTranscriptIntoPhrases } from './phrases'
import { phrasesCsv, phrasesJson } from './phraseExports'

const transcript = normalizeTranscript({ language: 'es', segments: [{ start: 0, end: 2, text: '', words: [
  { id: 'w1', text: '¿Qué,', start: 0, end: 0.5 },
  { id: 'w2', text: ' pasó?\n"Sí"', start: 0.6, end: 2 },
] }] })

describe('phrase exports', () => {
  it('exports project/audio context, phrase durations and original word timestamps as JSON', () => {
    const data = groupTranscriptIntoPhrases(transcript)
    const payload = JSON.parse(phrasesJson('project-1', 'voice.wav', 4, transcript, data))
    expect(payload).toMatchObject({ project_id: 'project-1', audio_id: 'voice.wav', audio_duration: 4, language: 'es' })
    expect(payload.phrases[0]).toMatchObject({ phrase_id: 'F001', start: 0, end: 0.5, duration: 0.5, text: '¿Qué,' })
    expect(payload.phrases[1].words[0]).toMatchObject({ word_id: 'w2', text: ' pasó?\n"Sí"', start: 0.6, end: 2 })
  })

  it('exports Excel-compatible UTF-8 CSV with RFC 4180 escaping', () => {
    const csv = phrasesCsv(groupTranscriptIntoPhrases(transcript))
    expect(csv.charCodeAt(0)).toBe(0xfeff)
    expect(csv).toContain('"phrase_id","start","end","duration","text"\r\n')
    expect(csv).toContain('"¿Qué,"')
    expect(csv).toContain('"pasó?\n""Sí"""')
  })
})
