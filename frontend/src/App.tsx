import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ApiError,
  createProject,
  deleteProject,
  deleteProjectAudio,
  getHealth,
  importProjectFile,
  listProjects,
  openProject,
  updateProjectDocument,
  type HealthResponse,
  type AudioInfo,
  type MediaInfo,
  type ProjectDocument,
  type ProjectSummary,
  type TranscriptInfo,
} from './api/client'
import { EditorWorkspace, type EditorWorkspaceHandle } from './editor/EditorWorkspace'
import { DEFAULT_DESIGN, normalizeTranscript } from './editor/model'

type ConnectionState = 'checking' | 'online' | 'offline'

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>('checking')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [selected, setSelected] = useState<ProjectDocument | null>(null)
  const [projectName, setProjectName] = useState('')
  const [message, setMessage] = useState('Conectando con el backend local…')
  const [busy, setBusy] = useState(false)
  const [editorRevision, setEditorRevision] = useState(0)
  const editorRef = useRef<EditorWorkspaceHandle>(null)
  const onEditorStatus = useCallback((status: string) => setMessage(status), [])

  const refreshProjects = useCallback(async () => setProjects(await listProjects()), [])
  const handleTranscriptionProjectUpdated = useCallback((updated: ProjectDocument) => {
    if (selected?.id !== updated.id) return
    setSelected(updated)
    setEditorRevision((value) => value + 1)
    setMessage('Transcripción generada y proyecto actualizado.')
    void refreshProjects().catch(showError)
  }, [refreshProjects, selected?.id])

  useEffect(() => {
    let active = true
    Promise.all([getHealth(), listProjects()]).then(
      ([status, result]) => {
        if (!active) return
        setHealth(status)
        setProjects(result)
        setConnection('online')
        setMessage('Backend conectado')
      },
      (error: unknown) => {
        if (!active) return
        setConnection('offline')
        setMessage(error instanceof ApiError ? error.message : 'No se pudo conectar con el backend.')
      },
    )
    return () => { active = false }
  }, [])

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!projectName.trim()) return
    setBusy(true)
    try {
      await editorRef.current?.flushPending()
      const project = await createProject(projectName.trim())
      setProjectName('')
      setSelected(await openProject(project.id))
      setEditorRevision((value) => value + 1)
      await refreshProjects()
      setMessage(`Proyecto “${project.name}” creado.`)
    } catch (error) { showError(error) } finally { setBusy(false) }
  }

  async function handleOpen(project: ProjectSummary) {
    if (selected?.id === project.id) return
    setBusy(true)
    try {
      await editorRef.current?.flushPending()
      setSelected(await openProject(project.id))
      setEditorRevision((value) => value + 1)
      setMessage(`Proyecto “${project.name}” abierto.`)
    } catch (error) { showError(error) } finally { setBusy(false) }
  }

  async function handleDelete(project: ProjectSummary) {
    if (!window.confirm(`¿Eliminar el proyecto “${project.name}” y todos sus archivos? Esta acción no se puede deshacer.`)) return
    setBusy(true)
    try {
      if (selected?.id === project.id) await editorRef.current?.flushPending()
      await deleteProject(project.id)
      if (selected?.id === project.id) {
        setSelected(null)
        setEditorRevision((value) => value + 1)
      }
      await refreshProjects()
      setMessage(`Proyecto “${project.name}” eliminado.`)
    } catch (error) { showError(error) } finally { setBusy(false) }
  }

  async function handleImport(kind: 'media' | 'audio' | 'transcription', file?: File) {
    if (!selected || !file) return
    setBusy(true)
    try {
      await editorRef.current?.flushPending()
      if (kind === 'media') await importProjectFile<MediaInfo>(selected.id, kind, file)
      else if (kind === 'audio') await importProjectFile<AudioInfo>(selected.id, kind, file)
      else await importProjectFile<TranscriptInfo>(selected.id, kind, file)
      let opened = await openProject(selected.id)
      if (kind === 'transcription' && opened.transcription) {
        const transcript = normalizeTranscript(opened.transcription)
        opened = await updateProjectDocument(opened.id, {
          ...opened.document,
          transcription: transcript,
          design: opened.document.design ?? { ...DEFAULT_DESIGN },
        })
        setEditorRevision((value) => value + 1)
      }
      setSelected(opened)
      if (kind === 'audio') setEditorRevision((value) => value + 1)
      await refreshProjects()
      setMessage(`${file.name} importado al proyecto.`)
    } catch (error) { showError(error) } finally { setBusy(false) }
  }

  async function handleDeleteAudio() {
    if (!selected) return
    setBusy(true)
    try {
      await editorRef.current?.flushPending()
      await deleteProjectAudio(selected.id)
      const updated = await openProject(selected.id)
      setSelected(updated)
      setEditorRevision((value) => value + 1)
      await refreshProjects()
      setMessage('Audio eliminado; la transcripción asociada también se eliminó.')
    } catch (error) { showError(error) } finally { setBusy(false) }
  }

  function showError(error: unknown) {
    setMessage(error instanceof Error ? error.message : 'Ocurrió un error inesperado.')
  }

  return <main className="studio-shell">
    <header className="topbar">
      <div className="brand-mark" aria-hidden="true">A/T</div>
      <div className="brand-title"><p className="eyebrow">ARMA-TE STUDIO</p><h1>Editor de subtítulos</h1></div>
      <div className={`connection connection--${connection}`} role="status" aria-live="polite"><span className="connection-dot" />{message}</div>
    </header>

    <div className="application-layout">
      <aside className="project-panel">
        <h2>Proyectos</h2>
        <form className="create-form" onSubmit={(event) => void handleCreate(event)}>
          <label htmlFor="project-name">Nuevo proyecto</label>
          <input id="project-name" value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={160} placeholder="Nombre del proyecto" />
          <button disabled={busy || connection !== 'online' || !projectName.trim()}>Crear proyecto</button>
        </form>
        <div className="project-list" aria-label="Proyectos existentes">
          {projects.length === 0 && <p className="muted">Aún no hay proyectos.</p>}
          {projects.map((project) => <div className={`project-entry${selected?.id === project.id ? ' project-entry--active' : ''}`} key={project.id}><button className="project-row" onClick={() => void handleOpen(project)} disabled={busy}>
            <strong>{project.name}</strong><span>{project.transcription_info ? `${project.transcription_info.segment_count} segmentos` : 'Sin transcripción'}</span>
          </button><button className="delete-project" type="button" aria-label={`Eliminar proyecto ${project.name}`} onClick={() => void handleDelete(project)} disabled={busy}>Eliminar</button></div>)}
        </div>
      </aside>

      <div className="editor-container">
        {!selected ? <section className="editor-empty app-welcome"><h2>Abre o crea un proyecto</h2><p>El editor sincroniza el video, las palabras, la timeline y la vista previa de subtítulos.</p></section> : <>
          <div className="project-file-actions">
            <span className="project-id">{selected.id}</span>
            <label className="button secondary">Importar MP4<input type="file" accept="video/mp4,.mp4" disabled={busy} onChange={(event) => { void handleImport('media', event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
            <label className="button secondary">{selected.audio ? 'Reemplazar audio' : 'Importar WAV/MP3'}<input type="file" accept="audio/wav,audio/x-wav,audio/mpeg,.wav,.mp3" disabled={busy} onChange={(event) => {
              const file = event.currentTarget.files?.[0]
              const hasTranscript = Boolean(selected.transcription ?? selected.document.transcription ?? selected.transcription_info)
              const needsConfirmation = Boolean(selected.audio || hasTranscript)
              const confirmation = selected.audio
                ? `¿Reemplazar el audio ${selected.audio.filename} por ${file?.name}? La transcripción actual se eliminará.`
                : 'Importar este audio invalidará la transcripción existente porque pertenece a otra fuente.'
              if (file && (!needsConfirmation || window.confirm(confirmation))) void handleImport('audio', file)
              event.currentTarget.value = ''
            }} /></label>
            {selected.audio && <>
              <span className="project-id">{selected.audio.filename}{selected.audio.duration ? ` · ${selected.audio.duration.toFixed(1)} s` : ''}</span>
              <button className="button secondary" type="button" disabled={busy} onClick={() => {
                if (window.confirm(`¿Eliminar ${selected.audio?.filename} y su transcripción asociada?`)) void handleDeleteAudio()
              }}>Eliminar audio</button>
            </>}
            <label className="button secondary">Importar JSON<input type="file" accept="application/json,.json" disabled={busy} onChange={(event) => { void handleImport('transcription', event.target.files?.[0]); event.currentTarget.value = '' }} /></label>
          </div>
          <EditorWorkspace
            key={`${selected.id}-${editorRevision}`}
            ref={editorRef}
            project={selected}
            onStatus={onEditorStatus}
            onTranscriptionProjectUpdated={handleTranscriptionProjectUpdated}
          />
        </>}
      </div>
    </div>
    {health && <footer>{health.application} · v{health.version}</footer>}
  </main>
}
