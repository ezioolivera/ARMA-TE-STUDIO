import type { Transcript, Word } from './model'

export interface Phrase {
  id: string
  text: string
  start: number
  end: number
  wordIds: string[]
}

export interface PhraseTranscription {
  version: 1
  pauseThreshold: number
  phrases: Phrase[]
}

const END_PUNCTUATION = /[.,;:!?]+["'”’»)}\]]*$/u

export function wordsInTranscript(transcript: Transcript): Word[] {
  return transcript.segments.flatMap((segment) => segment.words)
}

export function phraseTextFromWords(words: Word[]): string {
  let text = ''
  for (const word of words) {
    const token = word.text
    if (!token) continue
    if (!text || /^\s/u.test(token) || /^[,.;:!?…%)\]}]/u.test(token)) text += token
    else text += ` ${token}`
  }
  return text.trim()
}

function phraseId(index: number): string {
  return `F${String(index + 1).padStart(3, '0')}`
}

function createPhrase(words: Word[], index: number): Phrase {
  const first = words[0]
  const last = words[words.length - 1]
  return {
    id: phraseId(index),
    text: phraseTextFromWords(words),
    start: first.start,
    end: last.end,
    wordIds: words.map((word) => word.id),
  }
}

export function groupTranscriptIntoPhrases(transcript: Transcript, pauseThreshold = 0.6): PhraseTranscription {
  const words = wordsInTranscript(transcript)
  const threshold = Number.isFinite(pauseThreshold) ? Math.max(0, pauseThreshold) : 0.6
  const groups: Word[][] = []
  let current: Word[] = []
  for (let index = 0; index < words.length; index += 1) {
    const word = words[index]
    current.push(word)
    const next = words[index + 1]
    if (!next) break
    const pause = next.start - word.end
    if (END_PUNCTUATION.test(word.text.trim()) || pause >= threshold) {
      groups.push(current)
      current = []
    }
  }
  if (current.length) groups.push(current)
  return {
    version: 1,
    pauseThreshold: threshold,
    phrases: groups.map(createPhrase),
  }
}

export function restorePhraseTranscription(value: unknown, transcript: Transcript): PhraseTranscription | null {
  if (!value || typeof value !== 'object') return null
  const source = value as Partial<PhraseTranscription>
  if (source.version !== 1 || !Array.isArray(source.phrases)) return null
  const words = wordsInTranscript(transcript)
  const wordIds = words.map((word) => word.id)
  const restored: Phrase[] = []
  for (const raw of source.phrases) {
    if (!raw || typeof raw !== 'object') return null
    const phrase = raw as Phrase
    if (typeof phrase.id !== 'string' || typeof phrase.text !== 'string'
      || !Number.isFinite(phrase.start) || !Number.isFinite(phrase.end) || phrase.start < 0 || phrase.end <= phrase.start
      || !Array.isArray(phrase.wordIds) || phrase.wordIds.length === 0
      || phrase.wordIds.some((id) => typeof id !== 'string')) return null
    restored.push({ ...phrase, wordIds: [...phrase.wordIds] })
  }
  const referenced = restored.flatMap((phrase) => phrase.wordIds)
  if (referenced.length !== wordIds.length || referenced.some((id, index) => id !== wordIds[index])) return null
  const pauseThreshold = Number(source.pauseThreshold)
  return { version: 1, pauseThreshold: Number.isFinite(pauseThreshold) && pauseThreshold >= 0 ? pauseThreshold : 0.6, phrases: restored }
}

function reindex(phrases: Phrase[]): Phrase[] {
  return phrases.map((phrase, index) => ({ ...phrase, id: phraseId(index), wordIds: [...phrase.wordIds] }))
}

export function splitPhrase(data: PhraseTranscription, phraseIdToSplit: string, afterWordIndex: number, transcript: Transcript): PhraseTranscription {
  const phraseIndex = data.phrases.findIndex((phrase) => phrase.id === phraseIdToSplit)
  if (phraseIndex < 0) return data
  const phrase = data.phrases[phraseIndex]
  if (!Number.isInteger(afterWordIndex) || afterWordIndex < 1 || afterWordIndex >= phrase.wordIds.length) return data
  const wordMap = new Map(wordsInTranscript(transcript).map((word) => [word.id, word]))
  const leftIds = phrase.wordIds.slice(0, afterWordIndex)
  const rightIds = phrase.wordIds.slice(afterWordIndex)
  const leftLast = wordMap.get(leftIds[leftIds.length - 1])
  const rightFirst = wordMap.get(rightIds[0])
  if (!leftLast || !rightFirst) return data
  const split: Phrase[] = [
    { ...phrase, text: phraseTextFromWords(leftIds.map((id) => wordMap.get(id)!).filter(Boolean)), end: leftLast.end, wordIds: leftIds },
    { id: '', text: phraseTextFromWords(rightIds.map((id) => wordMap.get(id)!).filter(Boolean)), start: rightFirst.start, end: phrase.end, wordIds: rightIds },
  ]
  const phrases = [...data.phrases.slice(0, phraseIndex), ...split, ...data.phrases.slice(phraseIndex + 1)]
  return { ...data, phrases: reindex(phrases) }
}

export function mergePhrases(data: PhraseTranscription, phraseIdToMerge: string): PhraseTranscription {
  const index = data.phrases.findIndex((phrase) => phrase.id === phraseIdToMerge)
  if (index < 0 || index + 1 >= data.phrases.length) return data
  const left = data.phrases[index]
  const right = data.phrases[index + 1]
  const merged: Phrase = {
    ...left,
    text: `${left.text.trimEnd()} ${right.text.trimStart()}`.trim(),
    end: right.end,
    wordIds: [...left.wordIds, ...right.wordIds],
  }
  const phrases = [...data.phrases.slice(0, index), merged, ...data.phrases.slice(index + 2)]
  return { ...data, phrases: reindex(phrases) }
}

export function updatePhrase(data: PhraseTranscription, phraseIdToUpdate: string, patch: Partial<Pick<Phrase, 'text' | 'start' | 'end'>>): PhraseTranscription {
  return {
    ...data,
    phrases: data.phrases.map((phrase) => {
      if (phrase.id !== phraseIdToUpdate) return phrase
      const next = { ...phrase, ...patch }
      if (!Number.isFinite(next.start) || !Number.isFinite(next.end) || next.start < 0 || next.end <= next.start) return phrase
      return next
    }),
  }
}

export function phraseWords(phrase: Phrase, transcript: Transcript): Word[] {
  const words = new Map(wordsInTranscript(transcript).map((word) => [word.id, word]))
  return phrase.wordIds.map((id) => words.get(id)).filter((word): word is Word => Boolean(word))
}

export function phraseDuration(phrase: Phrase): number {
  return phrase.end - phrase.start
}
