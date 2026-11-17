import { describe, expect, it } from 'vitest'
import { composeCaptionLines } from './captionLayout'
import { buildAssLayout } from './exportLayout'
import { formatSubtitleText, normalizeDesign, type Word } from './model'

describe('buildAssLayout', () => {
  it('sends the source-to-preview scale used to normalize font and stroke pixels', () => {
    const transcript = {
      segments: [{
        id: 'segment-1', start: 0, end: 1, text: 'Acción, niño',
        words: [
          { id: 'word-1', text: 'Acción,', start: 0, end: 0.5 },
          { id: 'word-2', text: 'niño', start: 0.5, end: 1 },
        ],
      }],
    }
    const layout = buildAssLayout(
      transcript,
      normalizeDesign({ fontFamily: 'Anton', fontSize: 96, maxWidth: 85 }),
      1080,
      405,
      (fontSize, text) => fontSize * text.length * 0.5,
    )

    expect(layout).toHaveLength(2)
    expect(layout.map((cue) => cue.render_scale)).toEqual([2.666667, 2.666667])
    expect(layout.map((cue) => cue.active_word_id)).toEqual(['word-1', 'word-2'])
  })

  it('uses the same formatted composition as the live preview without changing source words', () => {
    const words: Word[] = [
      { id: 'word-mi', text: 'mi', start: 0, end: 0.3 },
      { id: 'word-nino', text: ' niño', start: 0.3, end: 0.7 },
    ]
    const transcript = {
      segments: [{ id: 'segment-1', start: 0, end: 0.7, text: 'mi niño', words }],
    }
    const before = JSON.stringify(transcript)
    const design = normalizeDesign({
      fontFamily: 'Anton', fontSize: 56, maxWidth: 30, wordSpacing: 0.16,
      textFormat: 'uppercase',
    })
    const measure = (_fontSize: number, text: string) => Array.from(text).reduce(
      (width, character) => width + (character === 'I' ? 2 : 1), 0,
    ) * 4
    const previewComposition = composeCaptionLines(
      words,
      'word-mi',
      30,
      10,
      (_fontSize, text) => measure(10, text),
      (fontSize) => fontSize * design.wordSpacing,
      (word) => formatSubtitleText(word.text.trim(), design.textFormat),
    )
    const manifest = buildAssLayout(transcript, design, 1080, 100, measure)

    expect(manifest[0].lines).toEqual(previewComposition.lines.map((line) => line.map((word) => word.id)))
    expect(manifest[0].lines).toEqual([['word-mi'], ['word-nino']])
    expect(manifest.map((cue) => cue.active_word_id)).toEqual(['word-mi', 'word-nino'])
    expect(words.map(({ id, text, start, end }) => ({ id, text, start, end }))).toEqual([
      { id: 'word-mi', text: 'mi', start: 0, end: 0.3 },
      { id: 'word-nino', text: ' niño', start: 0.3, end: 0.7 },
    ])
    expect(JSON.stringify(transcript)).toBe(before)
  })
})
