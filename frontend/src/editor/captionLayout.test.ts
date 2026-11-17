import { describe, expect, it } from 'vitest'
import { composeCaptionLines, selectCaptionWindow, wrapCaptionWords } from './captionLayout'
import { formatSubtitleText, type TextFormat, type Word } from './model'

const words: Word[] = ['ARMA', 'TE', 'SUBTITLE', 'STUDIO'].map((text, index) => ({
  id: `word-${index}`, text: ` ${text}`, start: index, end: index + 0.5,
}))

describe('caption line layout', () => {
  it('wraps real word tokens into at most two measured lines without losing their IDs', () => {
    const lines = wrapCaptionWords(words, 18, (text) => text.length * 2, 1)
    expect(lines).toHaveLength(2)
    expect(lines.flat().map((word) => word.id)).toEqual(words.map((word) => word.id))
  })

  it('keeps a single long word intact and leaves empty captions empty', () => {
    expect(wrapCaptionWords([words[2]], 2, (text) => text.length * 10)).toEqual([[words[2]]])
    expect(wrapCaptionWords([], 100, () => 0)).toEqual([])
  })

  it('keeps the live phrase centered on the active word and expands by measured width', () => {
    const phrase = selectCaptionWindow(words, 'word-2', 24, (text) => text.length * 2)
    expect(phrase.map((word) => word.id)).toContain('word-2')
    expect(phrase[0].id).toBe('word-1')
    expect(phrase.at(-1)?.id).toBe('word-3')
  })

  it('composes from the exact display text for every format without mutating words', () => {
    const sourceWords: Word[] = [
      { id: 'id-mi', text: 'mi', start: 1.25, end: 1.5 },
      { id: 'id-nino', text: ' niño', start: 1.5, end: 2.1 },
    ]
    const sourceSnapshot = JSON.stringify(sourceWords)
    const modes: TextFormat[] = ['original', 'uppercase', 'lowercase', 'title']
    const visibleLines = new Map<TextFormat, string[]>()
    const metric = (text: string) => Array.from(text).reduce(
      (width, character) => width + (character === 'I' ? 2 : 1), 0,
    ) * 4

    for (const mode of modes) {
      const composition = composeCaptionLines(
        sourceWords,
        'id-mi',
        30,
        10,
        (_size, text) => metric(text),
        () => 0,
        (word) => formatSubtitleText(word.text.trim(), mode),
      )
      visibleLines.set(mode, composition.lines.map((line) => line
        .map((word) => formatSubtitleText(word.text.trim(), mode)).join(' ')))
      expect(composition.lines.flat()).toEqual(sourceWords)
      expect(composition.lines.flat().map(({ id, start, end }) => ({ id, start, end }))).toEqual([
        { id: 'id-mi', start: 1.25, end: 1.5 },
        { id: 'id-nino', start: 1.5, end: 2.1 },
      ])
    }

    expect(visibleLines.get('original')).toEqual(['mi niño'])
    expect(visibleLines.get('uppercase')).toEqual(['MI', 'NIÑO'])
    expect(visibleLines.get('lowercase')).toEqual(['mi niño'])
    expect(visibleLines.get('title')).toEqual(['Mi Niño'])
    expect(JSON.stringify(sourceWords)).toBe(sourceSnapshot)
  })

  it('uses formatted words while selecting the active phrase and preserves its order', () => {
    const sourceWords: Word[] = [
      { id: 'one', text: 'mi', start: 0, end: 0.2 },
      { id: 'two', text: ' niño', start: 0.2, end: 0.5 },
      { id: 'three', text: ' hoy', start: 0.5, end: 0.8 },
    ]
    const upper = (word: Word) => formatSubtitleText(word.text.trim(), 'uppercase')
    const selected = selectCaptionWindow(
      sourceWords,
      'two',
      30,
      (text) => Array.from(text).reduce((width, character) => width + (character === 'I' ? 2 : 1), 0) * 4,
      0,
      upper,
    )
    expect(selected.map((word) => word.id)).toEqual(['one', 'two'])
    expect(selected.map((word) => word.text.trim())).toEqual(['mi', 'niño'])
  })
})
