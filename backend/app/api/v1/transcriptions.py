"""Project transcription jobs powered by the existing local engine."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.jobs import InvalidJobTransition, JobManager, JobRecord, JobState
from app.persistence import AutosaveCoordinator
from app.projects.models import TranscriptSummary
from app.projects.repository import ProjectRepository
from app.transcription_service import transcribe_media
from transcription import TranscriptionConfig, to_json

from .projects import _persist_snapshot, _project_lock, _record_or_404, _validate_transcription

router = APIRouter(tags=["transcriptions"])


class StartTranscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_size: Literal["tiny", "base", "small", "medium"] = "small"
    language: str | None = None


class TranscriptionConflict(RuntimeError):
    """The project media or transcript changed while inference was running."""


def _job_payload(record: JobRecord) -> dict:
    error = record.error
    return {
        "job_id": record.job_id,
        "state": record.state.value,
        "progress": record.progress,
        "message": record.message,
        "cancel_requested": record.cancel_requested,
        "error": None if error is None else {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        },
    }


def _has_transcription(record: dict) -> bool:
    document = record.get("document")
    document_transcription = document.get("transcription") if isinstance(document, dict) else None
    return record.get("transcription") is not None or document_transcription is not None


def _validate_generated_result(result) -> tuple[dict, TranscriptSummary]:
    payload, summary = _validate_transcription(json.loads(to_json(result)))
    for index, segment in enumerate(payload["segments"]):
        if segment["end"] <= segment["start"]:
            raise ValueError(f"Segment {index + 1} must have a positive duration")
        for word_index, word in enumerate(segment.get("words", [])):
            if word["end"] <= word["start"]:
                raise ValueError(
                    f"Word {word_index + 1} in segment {index + 1} must have a positive duration"
                )
    return payload, summary


async def _run_transcription_job(
    *,
    project_id: str,
    job_id: str,
    media_path: Path,
    media_identity: tuple[str, str, int, int],
    media_filename: str,
    config: TranscriptionConfig,
    repository: ProjectRepository,
    autosave: AutosaveCoordinator,
    project_locks: dict[str, asyncio.Lock],
    manager: JobManager,
) -> None:
    try:
        manager.start(job_id, "Cargando el modelo y preparando la transcripción.")
        manager.update_progress(job_id, 0, "Transcribiendo; el motor no informa porcentaje intermedio.")
        result = await transcribe_media(media_path, config)
        payload, summary = _validate_generated_result(result)
        generated_name = Path(media_filename).with_suffix(".json").name
        summary = summary.model_copy(update={"filename": generated_name})

        async with project_locks.setdefault(project_id, asyncio.Lock()):
            latest = _record_or_404(repository, project_id)
            current_media = latest.get("media")
            if not isinstance(current_media, dict) or (
                current_media.get("filename"), current_media.get("relative_path")
            ) != media_identity[:2]:
                raise TranscriptionConflict("El video del proyecto cambió durante la transcripción.")
            try:
                current_path = repository.media_path(project_id, current_media["filename"]).resolve(strict=True)
                current_stat = current_path.stat()
            except (ValueError, OSError, KeyError) as exc:
                raise TranscriptionConflict("El video del proyecto cambió durante la transcripción.") from exc
            if (current_stat.st_size, current_stat.st_mtime_ns) != media_identity[2:]:
                raise TranscriptionConflict("El archivo de video cambió durante la transcripción.")
            if _has_transcription(latest):
                raise TranscriptionConflict(
                    "El proyecto ya recibió una transcripción mientras se ejecutaba el trabajo."
                )

            document = latest.get("document")
            latest["document"] = {
                **(document if isinstance(document, dict) else {}),
                "transcription": payload,
            }
            latest["transcription_info"] = summary.model_dump()
            await _persist_snapshot(repository, autosave, project_id, latest)

        manager.succeed(job_id, "Transcripción generada y guardada en el proyecto.")
    except TranscriptionConflict as exc:
        current = manager.get(job_id)
        if current and current.state in {JobState.QUEUED, JobState.RUNNING}:
            manager.fail(job_id, "transcription_conflict", str(exc), retryable=False)
    except ValueError as exc:
        current = manager.get(job_id)
        if current and current.state in {JobState.QUEUED, JobState.RUNNING}:
            message = str(exc).strip() or "El motor devolvió tiempos de transcripción inválidos."
            manager.fail(job_id, "invalid_transcription_result", message, retryable=False)
    except Exception as exc:
        current = manager.get(job_id)
        if current and current.state in {JobState.QUEUED, JobState.RUNNING}:
            message = str(exc).strip() or "No se pudo generar la transcripción."
            manager.fail(job_id, "transcription_failed", message, retryable=True)


def _forget_task(job_id: str, project_id: str, request) -> None:
    state = request.app.state
    state.transcription_tasks.pop(job_id, None)
    if state.active_transcription_projects.get(project_id) == job_id:
        state.active_transcription_projects.pop(project_id, None)


@router.post("/projects/{project_id}/transcription-jobs")
async def start_transcription_job(
    project_id: str,
    payload: StartTranscriptionRequest,
    request: Request,
):
    repository: ProjectRepository = request.app.state.project_repository
    autosave: AutosaveCoordinator = request.app.state.autosave
    manager: JobManager = request.app.state.transcription_job_manager
    lock = _project_lock(request, project_id)
    try:
        config = TranscriptionConfig(model_size=payload.model_size, language=payload.language)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with lock:
        record = _record_or_404(repository, project_id)
        media = record.get("media")
        if not isinstance(media, dict) or not isinstance(media.get("filename"), str):
            raise HTTPException(status_code=422, detail="Importa un video MP4 antes de transcribir.")
        filename = media["filename"]
        if Path(filename).suffix.lower() != ".mp4":
            raise HTTPException(status_code=422, detail="La transcripción requiere un archivo MP4.")
        if media.get("probe_error"):
            raise HTTPException(status_code=422, detail="No se pudieron validar los streams del video.")
        if media.get("has_audio") is not True:
            raise HTTPException(status_code=422, detail="El video importado no contiene una pista de audio.")
        if _has_transcription(record):
            raise HTTPException(status_code=409, detail="El proyecto ya tiene una transcripción.")
        active_jobs = request.app.state.active_transcription_projects
        if project_id in active_jobs:
            raise HTTPException(status_code=409, detail="Ya hay una transcripción ejecutándose para este proyecto.")
        try:
            media_path = repository.media_path(project_id, filename).resolve(strict=True)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail="No se encuentra el MP4 importado.") from exc
        media_directory = (repository._directory(project_id) / "media").resolve()
        if media_path.parent != media_directory or not media_path.is_file():
            raise HTTPException(status_code=422, detail="La ruta del MP4 del proyecto no es válida.")

        relative_path = media.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise HTTPException(status_code=422, detail="El MP4 no tiene una ruta de proyecto válida.")
        media_stat = media_path.stat()
        job = manager.create("transcription", "En cola para transcripción.")
        task = asyncio.create_task(_run_transcription_job(
            project_id=project_id,
            job_id=job.job_id,
            media_path=media_path,
            media_identity=(filename, relative_path, media_stat.st_size, media_stat.st_mtime_ns),
            media_filename=filename,
            config=config,
            repository=repository,
            autosave=autosave,
            project_locks=request.app.state.project_locks,
            manager=manager,
        ))
        request.app.state.transcription_tasks[job.job_id] = task
        active_jobs[project_id] = job.job_id
        task.add_done_callback(lambda _: _forget_task(job.job_id, project_id, request))

    return JSONResponse(_job_payload(job), status_code=202)


@router.get("/transcription-jobs/{job_id}")
async def get_transcription_job(job_id: str, request: Request):
    manager: JobManager = request.app.state.transcription_job_manager
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No se encontró el trabajo de transcripción.")
    return _job_payload(record)
