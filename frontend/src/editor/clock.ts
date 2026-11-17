/** Small observable clock: frame-level updates notify only playback subscribers. */
export class PlaybackClock {
  private time = 0
  private readonly listeners = new Set<() => void>()

  readonly getSnapshot = (): number => this.time

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  set(time: number): void {
    if (!Number.isFinite(time) || time < 0 || Math.abs(time - this.time) < 0.0001) return
    this.time = time
    this.listeners.forEach((listener) => listener())
  }
}
