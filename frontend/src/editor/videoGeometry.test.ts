import { describe, expect, it } from 'vitest'
import {
  dragNormalizedPosition,
  getContainedVideoRect,
  getSmartVideoGuides,
  snapCaptionAxisToGuides,
} from './videoGeometry'

describe('video content geometry', () => {
  it('finds the visible area of a vertical video inside a wider fullscreen stage', () => {
    expect(getContainedVideoRect(
      { left: 0, top: 0, width: 800, height: 544 },
      { left: 0, top: 0, width: 800, height: 544 },
      1080,
      1920,
    )).toEqual({ left: 247, top: 0, width: 306, height: 544 })
  })

  it('finds the visible area of a horizontal video inside a taller stage', () => {
    expect(getContainedVideoRect(
      { left: 0, top: 0, width: 800, height: 900 },
      { left: 0, top: 0, width: 800, height: 900 },
      1920,
      1080,
    )).toEqual({ left: 0, top: 225, width: 800, height: 450 })
  })

  it('keeps the content rect equal to the stage when their aspect ratios match', () => {
    expect(getContainedVideoRect(
      { left: 30, top: 40, width: 800, height: 450 },
      { left: 30, top: 40, width: 800, height: 450 },
      1920,
      1080,
    )).toEqual({ left: 0, top: 0, width: 800, height: 450 })
  })

  it('converts pointer movement using the video content dimensions and normalized X/Y', () => {
    expect(dragNormalizedPosition(37, 66, 53, 30, 27.2, { width: 306, height: 544 })).toEqual({
      positionX: 37 + (30 / 306 * 100) / 0.47,
      positionY: 71,
    })
  })

  it('preserves normalized coordinates when the measured content rect changes on fullscreen transitions', () => {
    const normalized = { positionX: 37.4234, positionY: 66.3829 }
    const normalRect = getContainedVideoRect(
      { left: 0, top: 0, width: 376, height: 668.444 },
      { left: 0, top: 0, width: 376, height: 668.444 },
      1080,
      1920,
    )!
    const fullRect = getContainedVideoRect(
      { left: 0, top: 0, width: 800, height: 544 },
      { left: 0, top: 0, width: 800, height: 544 },
      1080,
      1920,
    )!
    const normalPixelPosition = {
      left: normalRect.left + normalRect.width * (normalized.positionX * (100 - 53) / 100) / 100,
      top: normalRect.top + normalRect.height * normalized.positionY / 100,
    }
    const fullPixelPosition = {
      left: fullRect.left + fullRect.width * (normalized.positionX * (100 - 53) / 100) / 100,
      top: fullRect.top + fullRect.height * normalized.positionY / 100,
    }

    expect((normalPixelPosition.left - normalRect.left) / normalRect.width)
      .toBeCloseTo((fullPixelPosition.left - fullRect.left) / fullRect.width)
    expect((normalPixelPosition.top - normalRect.top) / normalRect.height)
      .toBeCloseTo((fullPixelPosition.top - fullRect.top) / fullRect.height)
    expect(normalized).toEqual({ positionX: 37.4234, positionY: 66.3829 })
  })

  it('exposes center, edge, and 5 percent safe-margin guides for both axes', () => {
    expect(getSmartVideoGuides().map(({ id, percent }) => [id, percent])).toEqual([
      ['x-edge-start', 0], ['x-safe-start', 5], ['x-center', 50], ['x-safe-end', 95], ['x-edge-end', 100],
      ['y-edge-start', 0], ['y-safe-start', 5], ['y-center', 50], ['y-safe-end', 95], ['y-edge-end', 100],
    ])
  })

  it('snaps the caption center to horizontal and vertical video centers', () => {
    expect(snapCaptionAxisToGuides(41, 20, 100, 'x', { enabled: true })).toMatchObject({
      delta: -1, guideId: 'x-center', distance: 1,
    })
    expect(snapCaptionAxisToGuides(40, 20, 100, 'y', { enabled: true })).toMatchObject({
      delta: 0, guideId: 'y-center', distance: 0,
    })
  })

  it('snaps left, right, top, and bottom anchors to video edges', () => {
    expect(snapCaptionAxisToGuides(1, 20, 100, 'x', { enabled: true })).toMatchObject({ delta: -1, guideId: 'x-edge-start' })
    expect(snapCaptionAxisToGuides(79, 20, 100, 'x', { enabled: true })).toMatchObject({ delta: 1, guideId: 'x-edge-end' })
    expect(snapCaptionAxisToGuides(1, 20, 100, 'y', { enabled: true })).toMatchObject({ delta: -1, guideId: 'y-edge-start' })
    expect(snapCaptionAxisToGuides(79, 20, 100, 'y', { enabled: true })).toMatchObject({ delta: 1, guideId: 'y-edge-end' })
  })

  it('snaps left, right, top, and bottom anchors to five percent safe margins', () => {
    expect(snapCaptionAxisToGuides(6, 20, 100, 'x', { enabled: true })).toMatchObject({ delta: -1, guideId: 'x-safe-start' })
    expect(snapCaptionAxisToGuides(74, 20, 100, 'x', { enabled: true })).toMatchObject({ delta: 1, guideId: 'x-safe-end' })
    expect(snapCaptionAxisToGuides(6, 20, 100, 'y', { enabled: true })).toMatchObject({ delta: -1, guideId: 'y-safe-start' })
    expect(snapCaptionAxisToGuides(74, 20, 100, 'y', { enabled: true })).toMatchObject({ delta: 1, guideId: 'y-safe-end' })
  })

  it('keeps guides active while snap is off and applies hysteresis to an active snap', () => {
    expect(snapCaptionAxisToGuides(41, 20, 100, 'x', { enabled: false })).toMatchObject({
      delta: 0, guideId: 'x-center', guideIds: ['x-center'], distance: 1,
    })
    expect(snapCaptionAxisToGuides(60, 20, 100, 'x', { enabled: true, previousGuideId: 'x-center' })).toMatchObject({
      delta: -10, guideId: 'x-center', distance: 10,
    })
    expect(snapCaptionAxisToGuides(60, 20, 100, 'x', { enabled: true })).toMatchObject({
      delta: 0, guideId: null, distance: null,
    })
  })

  it('gives a nearby center guide priority over an almost equal edge correction', () => {
    const result = snapCaptionAxisToGuides(40.8, 20, 100, 'x', { enabled: true })
    expect(result.guideId).toBe('x-center')
    expect(result.delta).toBeCloseTo(-0.8)
  })

  it('hides distant guides and reports every nearby reference on the same axis', () => {
    expect(snapCaptionAxisToGuides(30, 10, 100, 'x', { enabled: true }).guideIds).toEqual([])
    expect(snapCaptionAxisToGuides(1, 98, 100, 'x', { enabled: true }).guideIds).toEqual([
      'x-edge-start', 'x-safe-start', 'x-center', 'x-safe-end', 'x-edge-end',
    ])
  })
})
