import type { Transcript } from './model'
import { phraseDuration, phraseWords, type PhraseTranscription } from './phrases'

export function phrasesJson(projectId: string, audioId: string | null, audioDuration: number | null, transcript: Transcript, data: PhraseTranscription): string {
  const phrases = data.phrases.map((phrase) => ({
    phrase_id: phrase.id,
    text: phrase.text,
    start: phrase.start,
    end: phrase.end,
    duration: phraseDuration(phrase),
    words: phraseWords(phrase, transcript).map((word) => ({ word_id: word.id, ...word })),
  }))
  return JSON.stringify({
    project_id: projectId,
    audio_id: audioId,
    audio_duration: audioDuration,
    language: transcript.language ?? null,
    phrases,
  }, null, 2)
}

function csvCell(value: string | number): string {
  const text = String(value)
  return `"${text.replaceAll('"', '""')}"`
}

export function phrasesCsv(data: PhraseTranscription): string {
  const rows = [
    ['phrase_id', 'start', 'end', 'duration', 'text'],
    ...data.phrases.map((phrase) => [
      phrase.id,
      phrase.start.toFixed(3),
      phrase.end.toFixed(3),
      phraseDuration(phrase).toFixed(3),
      phrase.text,
    ]),
  ]
  return `\uFEFF${rows.map((row) => row.map(csvCell).join(',')).join('\r\n')}\r\n`
}

export function downloadText(filename: string, contents: string, type: string): void {
  const url = URL.createObjectURL(new Blob([contents], { type }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
