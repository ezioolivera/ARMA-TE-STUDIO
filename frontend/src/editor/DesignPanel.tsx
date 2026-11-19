import { memo, useSyncExternalStore } from 'react'
import { DEFAULT_DESIGN, designAtTime, FONT_CHOICES, type DesignProfile, type TemporalDesignEvent } from './model'
import type { PlaybackClock } from './clock'

interface Props { value: DesignProfile; onChange: (next: DesignProfile) => void; clock: PlaybackClock; onPositionChange: (axis: 'positionX' | 'positionY', value: number, atTime?: number) => void }

export const DesignPanel = memo(function DesignPanel({ value, onChange, clock, onPositionChange }: Props) {
  const set = <K extends keyof DesignProfile>(key: K, next: DesignProfile[K]) => onChange({ ...value, [key]: next })
  const updateEvent = (id: string, patch: Partial<TemporalDesignEvent>) => set('temporalEvents', value.temporalEvents.map((event) => {
    if (event.id !== id) return event
    const updated = { ...event, ...patch }
    if (patch.start !== undefined) updated.end = Math.max(updated.end, updated.start + 0.1)
    if (patch.end !== undefined) updated.end = Math.max(updated.end, updated.start + 0.1)
    return updated
  }))
  const addEvent = () => set('temporalEvents', [...value.temporalEvents, {
    id: `position-${globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`}`,
    start: Math.max(0, clock.getSnapshot()), end: Math.max(0, clock.getSnapshot()) + 6,
    position: 'top', positionX: 50, positionY: 20,
  }])
  return <aside className="design-panel">
    <h3>Diseño de subtítulos</h3>
    <section className="design-group"><h4>Texto y tipografía</h4><div className="design-group-fields">
    <label>Tipografía
      <select value={value.fontFamily} onChange={(event) => set('fontFamily', event.target.value)}>
        {FONT_CHOICES.map((font) => <option key={font.label} value={font.label}>{font.label}</option>)}
      </select>
    </label>
    <label>Formato del texto
      <select aria-label="Formato del texto" value={value.textFormat} onChange={(event) => set('textFormat', event.target.value as DesignProfile['textFormat'])}>
        <option value="original">Original</option><option value="uppercase">MAYÚSCULAS</option><option value="lowercase">minúsculas</option><option value="title">Tipo Título</option>
      </select>
    </label>
    </div></section>
    <section className="design-group"><h4>Tamaño y espaciado</h4><div className="design-group-fields">
    <label>Tamaño <span>{value.fontSize}px</span><input type="range" min="18" max="96" value={value.fontSize} onChange={(event) => set('fontSize', Number(event.target.value))} /></label>
    <label>Espaciado entre letras <span>{value.letterSpacing}px</span><input type="range" min="-2" max="8" step="0.5" value={value.letterSpacing} onChange={(event) => set('letterSpacing', Number(event.target.value))} /></label>
    <label>Espaciado entre palabras <span>{value.wordSpacing.toFixed(2)}em</span><input type="range" min="0" max="1" step="0.02" value={value.wordSpacing} onChange={(event) => set('wordSpacing', Number(event.target.value))} /></label>
    <label>Ancho máximo <span>{value.maxWidth}%</span><input type="range" min="30" max="100" value={value.maxWidth} onChange={(event) => set('maxWidth', Number(event.target.value))} /></label>
    </div></section>
    <section className="design-group"><h4>Color y apariencia</h4><div className="design-group-fields">
    <ColorControl label="Color del texto" value={value.textColor} onChange={(color) => set('textColor', color)} />
    <ColorControl label="Palabra activa" value={value.activeWordColor} onChange={(color) => set('activeWordColor', color)} />
    <ColorControl label="Contorno" value={value.outlineColor} onChange={(color) => set('outlineColor', color)} />
    <label>Grosor de contorno <span>{value.outlineWidth}px</span><input type="range" min="0" max="8" step="0.5" value={value.outlineWidth} onChange={(event) => set('outlineWidth', Number(event.target.value))} /></label>
    <label className="checkbox-control"><input type="checkbox" checked={value.shadow} onChange={(event) => set('shadow', event.target.checked)} /> Sombra</label>
    </div></section>
    <section className="design-group"><h4>Posición y animación</h4><div className="design-group-fields">
    <label>Alineación
      <select value={value.alignment} onChange={(event) => set('alignment', event.target.value as DesignProfile['alignment'])}>
        <option value="left">Izquierda</option><option value="center">Centrada</option><option value="right">Derecha</option>
      </select>
    </label>
    <PositionControls value={value} clock={clock} onPositionChange={onPositionChange} />
    <section className="temporal-design" aria-label="Posiciones temporales">
      <div className="temporal-design-heading"><strong>Posiciones temporales</strong><button className="subtle-button" type="button" onClick={addEvent}>Agregar cambio</button></div>
      {value.temporalEvents.map((event, index) => <fieldset className="temporal-event" key={event.id}>
        <legend>Cambio {index + 1}</legend>
        <div className="temporal-time-fields">
          <label>Inicio<input aria-label={`Inicio cambio ${index + 1}`} type="number" min="0" step="0.1" value={event.start} onChange={(input) => updateEvent(event.id, { start: Math.max(0, Number(input.target.value)) })} /></label>
          <label>Final<input aria-label={`Final cambio ${index + 1}`} type="number" min={event.start + 0.1} step="0.1" value={event.end} onChange={(input) => updateEvent(event.id, { end: Number(input.target.value) })} /></label>
        </div>
        <label>Posición
          <select aria-label={`Posición cambio ${index + 1}`} value={event.position} onChange={(input) => updateEvent(event.id, { position: input.target.value as TemporalDesignEvent['position'] })}>
            <option value="base">Posición base</option><option value="top">Superior</option><option value="center">Central</option><option value="bottom">Inferior</option><option value="custom">Personalizada</option>
          </select>
        </label>
        {event.position !== 'base' && <div className="temporal-time-fields">
          <label>X %<input aria-label={`X cambio ${index + 1}`} type="number" min="0" max="100" value={event.positionX} onChange={(input) => updateEvent(event.id, { position: 'custom', positionX: Math.min(100, Math.max(0, Number(input.target.value))) })} /></label>
          <label>Y %<input aria-label={`Y cambio ${index + 1}`} type="number" min="0" max="100" value={event.position === 'top' ? 20 : event.position === 'center' ? 50 : event.position === 'bottom' ? 82 : event.positionY} onChange={(input) => updateEvent(event.id, { position: 'custom', positionY: Math.min(100, Math.max(0, Number(input.target.value))) })} /></label>
        </div>}
        <button className="subtle-button danger-button" type="button" aria-label={`Eliminar cambio ${index + 1}`} onClick={() => set('temporalEvents', value.temporalEvents.filter((item) => item.id !== event.id))}>Eliminar cambio</button>
      </fieldset>)}
    </section>
    </div></section>
    <button className="subtle-button" type="button" onClick={() => onChange({ ...DEFAULT_DESIGN })}>Restablecer diseño</button>
  </aside>
})

const PositionControls = memo(function PositionControls({ value, clock, onPositionChange }: {
  value: DesignProfile
  clock: PlaybackClock
  onPositionChange: Props['onPositionChange']
}) {
  const time = useSyncExternalStore(clock.subscribe, clock.getSnapshot, clock.getSnapshot)
  const active = designAtTime(value, time)
  return <>
    <label>Posición X <span>{Math.round(active.positionX)}%</span><input aria-label="Posición X" type="range" min="0" max="100" value={active.positionX} onChange={(event) => onPositionChange('positionX', Number(event.target.value), time)} /></label>
    <label>Posición Y <span>{Math.round(active.positionY)}%</span><input aria-label="Posición Y" type="range" min="4" max="96" value={active.positionY} onChange={(event) => onPositionChange('positionY', Number(event.target.value), time)} /></label>
  </>
})

function ColorControl({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="color-control">{label}<input type="color" value={value} onChange={(event) => onChange(event.target.value)} /></label>
}
