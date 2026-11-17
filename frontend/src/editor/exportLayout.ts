import { composeCaptionLines } from './captionLayout'
import { FONT_CHOICES, formatSubtitleText, type DesignProfile, type Transcript } from './model'

export interface ExportCueLayout {
  active_word_id: string
  lines: string[][]
  font_size: number
  render_scale: number
}

export type ExportLayout = ExportCueLayout[]

export interface ExportLayoutDiagnostic {
  active_word_id: string
  font_family: string
  css_font_family: string
  font_weight: number
  font_loaded: boolean
  configured_font_size: number
  measured_css_font_size: number
  exported_font_size: number
  render_scale: number
  stage_width: number
  source_width: number
  max_width_css: number
  word_spacing_em: number
  lines: Array<{
    word_ids: string[]
    planner_text: string
    measured_text_width_css: number
    gap_width_css: number
    total_width_css: number
    total_width_source: number
  }>
}

/**
 * Computes each visible word window with the same browser metrics and wrapping
 * functions used by SubtitleOverlay, then scales its font size to source pixels.
 */
export function buildAssLayout(
  transcript: Transcript,
  design: DesignProfile,
  sourceWidth: number,
  stageWidth: number,
  injectedMeasure?: (fontSize: number, text: string) => number,
  onFirstCueDiagnostic?: (diagnostic: ExportLayoutDiagnostic) => void,
): ExportLayout {
  const width = Math.max(1, stageWidth)
  const videoWidth = Math.max(1, sourceWidth)
  const renderScale = videoWidth / width
  const font = FONT_CHOICES.find((choice) => choice.label === design.fontFamily) ?? FONT_CHOICES[0]
  const canvas = injectedMeasure ? null : document.createElement('canvas')
  const context = canvas?.getContext('2d')
  const measureAt = (fontSize: number, text: string) => {
    if (injectedMeasure) return injectedMeasure(fontSize, text)
    if (context) context.font = `${font.weight} ${fontSize}px ${font.css}`
    return context?.measureText(text).width ?? text.length * fontSize * .55
  }
  const maxWidth = width * design.maxWidth / 100
  const initialFontSize = Math.max(10, width * design.fontSize / 1080)
  const wordSpacing = (fontSize: number) => fontSize * design.wordSpacing
  const cues: ExportLayout = []

  for (const segment of transcript.segments) {
    for (const activeWord of segment.words) {
      const { lines, fontSize } = composeCaptionLines(
        segment.words, activeWord.id, maxWidth,
        initialFontSize,
        measureAt,
        wordSpacing,
        (word) => formatSubtitleText(word.text.trim(), design.textFormat),
      )
      const exportedFontSize = fontSize * videoWidth / width
      if (cues.length === 0 && onFirstCueDiagnostic) {
        onFirstCueDiagnostic({
          active_word_id: activeWord.id,
          font_family: font.label,
          css_font_family: font.css,
          font_weight: font.weight,
          font_loaded: typeof document !== 'undefined'
            ? document.fonts.check(`${font.weight} ${fontSize}px ${font.css}`)
            : false,
          configured_font_size: design.fontSize,
          measured_css_font_size: fontSize,
          exported_font_size: Math.round(exportedFontSize * 100) / 100,
          render_scale: Math.round(renderScale * 1_000_000) / 1_000_000,
          stage_width: width,
          source_width: videoWidth,
          max_width_css: maxWidth,
          word_spacing_em: design.wordSpacing,
          lines: lines.map((line) => {
            const text = line.map((word) => formatSubtitleText(word.text.trim(), design.textFormat)).join(' ')
            const measuredTextWidth = measureAt(fontSize, text)
            const gapWidth = Math.max(0, line.length - 1) * wordSpacing(fontSize)
            return {
              word_ids: line.map((word) => word.id),
              planner_text: text,
              measured_text_width_css: measuredTextWidth,
              gap_width_css: gapWidth,
              total_width_css: measuredTextWidth + gapWidth,
              total_width_source: (measuredTextWidth + gapWidth) * renderScale,
            }
          }),
        })
      }
      cues.push({
        active_word_id: activeWord.id,
        lines: lines.map((line) => line.map((word) => word.id)),
        font_size: Math.round(exportedFontSize * 100) / 100,
        render_scale: Math.round(renderScale * 1_000_000) / 1_000_000,
      })
    }
  }
  return cues
}
