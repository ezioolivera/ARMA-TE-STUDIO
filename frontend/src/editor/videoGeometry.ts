export interface VideoContentRect {
  left: number
  top: number
  width: number
  height: number
}

export interface VideoBoxRect {
  left: number
  top: number
  width: number
  height: number
}

export type SmartGuideAxis = 'x' | 'y'
export type SmartGuideKind = 'center' | 'safe-margin' | 'edge'

export interface SmartGuide {
  id: string
  axis: SmartGuideAxis
  percent: number
  kind: SmartGuideKind
}

export interface SmartGuideSnapResult {
  delta: number
  guideId: string | null
  guideIds: string[]
  distance: number | null
}

export interface SmartGuideSnapOptions {
  previousGuideId?: string | null
  enabled: boolean
  threshold?: number
  hysteresis?: number
}

const SMART_GUIDE_SPECS: SmartGuide[] = [
  { id: 'x-edge-start', axis: 'x', percent: 0, kind: 'edge' },
  { id: 'x-safe-start', axis: 'x', percent: 5, kind: 'safe-margin' },
  { id: 'x-center', axis: 'x', percent: 50, kind: 'center' },
  { id: 'x-safe-end', axis: 'x', percent: 95, kind: 'safe-margin' },
  { id: 'x-edge-end', axis: 'x', percent: 100, kind: 'edge' },
  { id: 'y-edge-start', axis: 'y', percent: 0, kind: 'edge' },
  { id: 'y-safe-start', axis: 'y', percent: 5, kind: 'safe-margin' },
  { id: 'y-center', axis: 'y', percent: 50, kind: 'center' },
  { id: 'y-safe-end', axis: 'y', percent: 95, kind: 'safe-margin' },
  { id: 'y-edge-end', axis: 'y', percent: 100, kind: 'edge' },
]

/** Guide positions are relative to the already measured visible-video rectangle. */
export function getSmartVideoGuides(): SmartGuide[] {
  return SMART_GUIDE_SPECS.map((guide) => ({ ...guide }))
}

/**
 * Aligns a dragged caption box to visible-video edges, 5% safe margins, or
 * center lines. The incoming rect and returned delta use CSS pixels.
 */
export function snapCaptionAxisToGuides(
  start: number,
  size: number,
  videoSize: number,
  axis: SmartGuideAxis,
  options: SmartGuideSnapOptions,
): SmartGuideSnapResult {
  if (size <= 0 || videoSize <= 0) return { delta: 0, guideId: null, guideIds: [], distance: null }

  const threshold = options.threshold ?? 8
  const hysteresis = Math.max(threshold, options.hysteresis ?? 12)
  const end = start + size
  const anchors = [
    start,
    start + size / 2,
    end,
  ]
  const guides = SMART_GUIDE_SPECS.filter((guide) => guide.axis === axis)
  const candidates = guides.flatMap((guide) => {
    const target = videoSize * guide.percent / 100
    return anchors.map((anchor) => ({
      guide,
      delta: target - anchor,
      distance: Math.abs(target - anchor),
    }))
  })

  const previous = options.previousGuideId
    ? candidates
      .filter((candidate) => candidate.guide.id === options.previousGuideId
        && candidate.distance <= hysteresis)
      .sort((left, right) => left.distance - right.distance)[0]
    : undefined
  const withinRange = candidates.filter((candidate) => candidate.distance <= threshold)
  const closest = withinRange.sort((left, right) => left.distance - right.distance
    || left.guide.percent - right.guide.percent)[0]
  const center = withinRange
    .filter((candidate) => candidate.guide.kind === 'center')
    .sort((left, right) => left.distance - right.distance)[0]
  const selected = previous ?? (center && closest && center.distance <= closest.distance + 1.5
    ? center
    : closest)

  if (!selected) {
    return {
      delta: 0,
      guideId: null,
      guideIds: guides.filter((guide) => withinRange.some((candidate) => candidate.guide.id === guide.id)).map((guide) => guide.id),
      distance: null,
    }
  }
  const delta = options.enabled ? selected.delta : 0
  const displayedAnchors = anchors.map((anchor) => anchor + delta)
  const visibleGuideIds = new Set(guides.filter((guide) => {
    const target = videoSize * guide.percent / 100
    return displayedAnchors.some((anchor) => Math.abs(target - anchor) <= threshold)
      || (guide.id === previous?.guide.id && displayedAnchors.some((anchor) => Math.abs(target - anchor) <= hysteresis))
  }).map((guide) => guide.id))
  const guideIds = guides.filter((guide) => visibleGuideIds.has(guide.id)).map((guide) => guide.id)
  return {
    delta,
    guideId: selected.guide.id,
    guideIds,
    distance: selected.distance,
  }
}

/** Returns the visible object-fit: contain video area relative to its stage. */
export function getContainedVideoRect(
  stage: VideoBoxRect,
  video: VideoBoxRect,
  sourceWidth: number,
  sourceHeight: number,
): VideoContentRect | null {
  if (video.width <= 0 || video.height <= 0 || sourceWidth <= 0 || sourceHeight <= 0) return null

  const sourceAspect = sourceWidth / sourceHeight
  const boxAspect = video.width / video.height
  const width = boxAspect > sourceAspect ? video.height * sourceAspect : video.width
  const height = boxAspect > sourceAspect ? video.height : video.width / sourceAspect
  const horizontalInset = (video.width - width) / 2
  const verticalInset = (video.height - height) / 2

  return {
    left: video.left - stage.left + horizontalInset,
    top: video.top - stage.top + verticalInset,
    width,
    height,
  }
}

/** Applies pointer movement in visible-video pixels to the existing normalized coordinates. */
export function dragNormalizedPosition(
  positionX: number,
  positionY: number,
  maxWidthPercent: number,
  deltaX: number,
  deltaY: number,
  videoRect: Pick<VideoContentRect, 'width' | 'height'>,
): { positionX: number; positionY: number } {
  if (videoRect.width <= 0 || videoRect.height <= 0) return { positionX, positionY }
  const usableX = Math.max(0.01, 1 - Math.min(100, Math.max(0, maxWidthPercent)) / 100)
  return {
    positionX: Math.max(0, Math.min(100, positionX + (deltaX / videoRect.width * 100) / usableX)),
    positionY: Math.max(4, Math.min(96, positionY + deltaY / videoRect.height * 100)),
  }
}
