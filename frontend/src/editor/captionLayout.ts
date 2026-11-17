import type { Word } from './model'

export type CaptionWordText = (word: Word) => string

export interface CaptionComposition {
  phrase: Word[]
  lines: Word[][]
  fontSize: number
}

const originalWordText: CaptionWordText = (word) => word.text.trim()

function lineText(words: Word[], displayText: CaptionWordText): string {
  return words.map(displayText).join(' ')
}

/**
 * Canonical word wrapping for the live preview and future subtitle renderers.
 * The caller supplies text metrics from the selected font so browser and export
 * backends can share line breaks without duplicating segmentation rules.
 */
export function wrapCaptionWords(
  words: Word[],
  maxWidth: number,
  measure: (text: string) => number,
  wordSpacingPx = 0,
  maxLines = 2,
  displayText: CaptionWordText = originalWordText,
): Word[][] {
  if (!words.length) return []
  const lines: Word[][] = [[]]
  const lineText: string[] = ['']

  for (const word of words) {
    const text = displayText(word)
    const lineIndex = lines.length - 1
    const candidate = lineText[lineIndex] ? `${lineText[lineIndex]} ${text}` : text
    const extraSpacing = Math.max(0, lines[lineIndex].length) * wordSpacingPx
    if (lineText[lineIndex] && measure(candidate) + extraSpacing > maxWidth && lines.length < maxLines) {
      lines.push([word])
      lineText.push(text)
    } else {
      lines[lineIndex].push(word)
      lineText[lineIndex] = candidate
    }
  }
  return lines
}

/** Selects the largest contiguous phrase around the active token that fits the visual safe width. */
export function selectCaptionWindow(
  words: Word[],
  activeWordId: string | undefined,
  maxWidth: number,
  measure: (text: string) => number,
  wordSpacingPx = 0,
  displayText: CaptionWordText = originalWordText,
  maxLines = 2,
): Word[] {
  if (!words.length) return []
  const activeIndex = Math.max(0, words.findIndex((word) => word.id === activeWordId))
  let start = activeIndex
  let end = activeIndex + 1
  let closedLeft = start === 0
  let closedRight = end === words.length

  const fits = (candidateStart: number, candidateEnd: number) => {
    const lines = wrapCaptionWords(
      words.slice(candidateStart, candidateEnd), maxWidth, measure, wordSpacingPx, maxLines, displayText,
    )
    return lines.every((line) => {
      const text = lineText(line, displayText)
      return measure(text) + Math.max(0, line.length - 1) * wordSpacingPx <= maxWidth
    })
  }

  while (!closedLeft || !closedRight) {
    const preferLeft = activeIndex - start <= end - 1 - activeIndex
    const tryLeft = preferLeft && !closedLeft || closedRight
    if (tryLeft) {
      if (fits(start - 1, end)) {
        start -= 1
        if (start === 0) closedLeft = true
      }
      else closedLeft = true
    } else if (!closedRight) {
      if (fits(start, end + 1)) {
        end += 1
        if (end === words.length) closedRight = true
      }
      else closedRight = true
    }
  }
  return words.slice(start, end)
}

/** Shared live-preview/export compositor. It formats only measured labels and returns original words. */
export function composeCaptionLines(
  words: Word[],
  activeWordId: string | undefined,
  maxWidth: number,
  initialFontSize: number,
  measureAt: (fontSize: number, text: string) => number,
  wordSpacingAt: (fontSize: number) => number,
  displayText: CaptionWordText = originalWordText,
  maxLines = 2,
): CaptionComposition {
  const phrase = selectCaptionWindow(
    words,
    activeWordId,
    maxWidth,
    (text) => measureAt(initialFontSize, text),
    wordSpacingAt(initialFontSize),
    displayText,
    maxLines,
  )
  let fontSize = initialFontSize
  let lines: Word[][] = []
  do {
    const spacing = wordSpacingAt(fontSize)
    lines = wrapCaptionWords(
      phrase,
      maxWidth,
      (text) => measureAt(fontSize, text),
      spacing,
      maxLines,
      displayText,
    )
    const fits = lines.length <= maxLines && lines.every((line) => (
      measureAt(fontSize, lineText(line, displayText))
      + Math.max(0, line.length - 1) * spacing <= maxWidth
    ))
    if (fits || fontSize <= 10) break
    fontSize = Math.max(10, fontSize - 1)
  } while (fontSize >= 10)

  return { phrase, lines, fontSize }
}
