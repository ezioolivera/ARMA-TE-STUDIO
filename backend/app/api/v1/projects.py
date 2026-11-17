"""Project, media, transcript and byte-range playback endpoints."""

from __future__ import annotations

import json
import asyncio
import copy
import math
import os
from pathlib import Path
import re
from uuid import uuid4
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.persistence import AutosaveCoordinator
from app.projects.audio import probe_audio, safe_audio_upload_name
from app.projects.media import InvalidMedia, copy_upload_to_file, probe_video, safe_upload_name
from app.projects.models import (
    AudioInfo,
    CreateProjectRequest,
    MediaInfo,
    ProjectDocument,
    ProjectSummary,
    TranscriptSummary,
    UpdateProjectRequest,
)
from app.projects.repository import ProjectNotFound, ProjectRepository

router = APIRouter(prefix="/projects", tags=["projects"])
MAX_TRANSCRIPT_BYTES = 50 * 1024 * 1024


def _services(request: Request) -> tuple[ProjectRepository, AutosaveCoordinator]:
    return request.app.state.project_repository, request.app.state.autosave


def _project_lock(request: Request, project_id: str) -> asyncio.Lock:
    return request.app.state.project_locks.setdefault(project_id, asyncio.Lock())


def _record_or_404(repository: ProjectRepository, project_id: str) -> dict[str, Any]:
    try:
        return repository.get(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _persist_snapshot(
    repository: ProjectRepository,
    autosave: AutosaveCoordinator,
    project_id: str,
    record: dict[str, Any],
) -> None:
    if not autosave.policy.enabled:
        await asyncio.to_thread(repository.save, project_id, record)
        return
    autosave.schedule(project_id, record)
    await autosave.flush(project_id)


def _ensure_no_active_transcription(request: Request, project_id: str) -> None:
    if project_id in request.app.state.active_transcription_projects:
        raise HTTPException(status_code=409, detail="Espera a que termine la transcripción antes de cambiar el audio.")


def _invalidate_audio_transcription(record: dict[str, Any]) -> None:
    document = record.get("document")
    if isinstance(document, dict):
        document = dict(document)
        document.pop("transcription", None)
        document.pop("phrase_transcription", None)
        record["document"] = document
    record["transcription"] = None
    record["transcription_info"] = None


async def _restore_project_snapshot(
    repository: ProjectRepository, autosave: AutosaveCoordinator, project_id: str, snapshot: dict[str, Any]
) -> None:
    restored = copy.deepcopy(snapshot)
    if autosave.policy.enabled:
        try:
            autosave.schedule(project_id, restored)
            await autosave.flush(project_id)
            return
        except Exception:
            pass
    await asyncio.to_thread(repository.save, project_id, restored)


def _summary(record: dict[str, Any]) -> ProjectSummary:
    return ProjectSummary.model_validate(record)


@router.post("", response_model=ProjectSummary, status_code=201)
async def create_project(payload: CreateProjectRequest, request: Request) -> ProjectSummary:
    repository, _ = _services(request)
    return _summary(repository.create(payload.name.strip()))


@router.get("", response_model=list[ProjectSummary])
async def list_projects(request: Request) -> list[ProjectSummary]:
    repository, _ = _services(request)
    return [_summary(record) for record in repository.list()]


@router.get("/{project_id}", response_model=ProjectDocument)
async def open_project(project_id: str, request: Request) -> ProjectDocument:
    repository, _ = _services(request)
    return ProjectDocument.model_validate(_record_or_404(repository, project_id))


@router.patch("/{project_id}", response_model=ProjectDocument)
async def update_project(
    project_id: str, payload: UpdateProjectRequest, request: Request
) -> ProjectDocument:
    repository, autosave = _services(request)
    async with _project_lock(request, project_id):
        record = _record_or_404(repository, project_id)
        changes = payload.model_dump(exclude_unset=True)
        for key, value in changes.items():
            if value is not None:
                record[key] = value
        await _persist_snapshot(repository, autosave, project_id, record)
        return ProjectDocument.model_validate(repository.get(project_id))


@router.delete("/{project_id}", response_model=dict[str, Any])
async def delete_project(project_id: str, request: Request) -> dict[str, Any]:
    repository, autosave = _services(request)
    async with _project_lock(request, project_id):
        _record_or_404(repository, project_id)
        try:
            # Drain any debounced snapshot before removing its destination directory.
            await autosave.flush(project_id)
            repository.delete(project_id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except OSError as exc:
            raise HTTPException(status_code=500, detail="Project files could not be deleted safely") from exc
    return {"id": project_id, "deleted": True}


@router.post("/{project_id}/media", response_model=MediaInfo, status_code=201)
async def import_media(
    project_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
) -> MediaInfo:
    repository, autosave = _services(request)
    _record_or_404(repository, project_id)
    try:
        safe_name = safe_upload_name(filename)
        destination = repository.media_path(project_id, safe_name)
        staged, _ = await copy_upload_to_file(request, destination)
        relative_path = str(destination.relative_to(repository.root).as_posix())
        try:
            media = await probe_video(staged, safe_name, relative_path)
        except FileNotFoundError as exc:
            # Keep the untouched original and make the missing local FFprobe visible.
            media = MediaInfo(
                filename=safe_name,
                relative_path=relative_path,
                probe_error=str(exc),
            )
        except InvalidMedia as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        os.replace(staged, destination)
    except HTTPException:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise
    except InvalidMedia as exc:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise

    async with _project_lock(request, project_id):
        record = _record_or_404(repository, project_id)
        record["media"] = media.model_dump()
        await _persist_snapshot(repository, autosave, project_id, record)
    return media


@router.post("/{project_id}/audio", response_model=AudioInfo, status_code=201)
async def import_audio(
    project_id: str,
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255),
) -> AudioInfo:
    repository, autosave = _services(request)
    current = _record_or_404(repository, project_id)
    _ensure_no_active_transcription(request, project_id)
    original_audio = copy.deepcopy(current.get("audio"))
    try:
        safe_name = safe_audio_upload_name(filename)
        destination = repository.media_path(project_id, safe_name)
        old_path = None
        if isinstance(original_audio, dict) and isinstance(original_audio.get("filename"), str):
            old_path = repository.media_path(project_id, original_audio["filename"])
        replacing_same_path = old_path is not None and old_path == destination
        if destination.exists() and not replacing_same_path:
            raise HTTPException(status_code=409, detail="Ya existe un archivo con ese nombre en el proyecto.")
        staged, _ = await copy_upload_to_file(request, destination, media_label="Audio")
        relative_path = str(destination.relative_to(repository.root).as_posix())
        audio = await probe_audio(staged, safe_name, relative_path)
    except HTTPException:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise
    except FileNotFoundError as exc:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="FFprobe no está disponible para validar el audio.") from exc
    except InvalidMedia as exc:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        if "staged" in locals():
            staged.unlink(missing_ok=True)
        raise

    staged_path = staged
    rollback_path = None
    committed = False
    try:
        async with _project_lock(request, project_id):
            _ensure_no_active_transcription(request, project_id)
            record = _record_or_404(repository, project_id)
            if record.get("audio") != original_audio:
                raise HTTPException(status_code=409, detail="El audio del proyecto cambió durante la carga. Inténtalo de nuevo.")
            if destination.exists() and not replacing_same_path:
                raise HTTPException(status_code=409, detail="Ya existe un archivo con ese nombre en el proyecto.")
            previous = copy.deepcopy(record)
            if replacing_same_path and destination.exists():
                rollback_path = destination.with_name(f".audio-rollback-{uuid4().hex}")
                os.replace(destination, rollback_path)
            os.replace(staged, destination)
            committed = True
            record["audio"] = audio.model_dump()
            _invalidate_audio_transcription(record)
            try:
                await _persist_snapshot(repository, autosave, project_id, record)
            except BaseException:
                destination.unlink(missing_ok=True)
                if rollback_path and rollback_path.exists():
                    os.replace(rollback_path, destination)
                await _restore_project_snapshot(repository, autosave, project_id, previous)
                committed = False
                raise
            if old_path is not None and old_path != destination:
                try:
                    old_path.unlink(missing_ok=True)
                except OSError:
                    pass
            if rollback_path:
                try:
                    rollback_path.unlink(missing_ok=True)
                except OSError:
                    pass
    except Exception:
        staged_path.unlink(missing_ok=True)
        if committed and destination.exists():
            destination.unlink(missing_ok=True)
        if rollback_path and rollback_path.exists() and not destination.exists():
            os.replace(rollback_path, destination)
        raise
    return audio


@router.delete("/{project_id}/audio", response_model=dict[str, Any])
async def delete_audio(project_id: str, request: Request) -> dict[str, Any]:
    repository, autosave = _services(request)
    _ensure_no_active_transcription(request, project_id)
    async with _project_lock(request, project_id):
        _ensure_no_active_transcription(request, project_id)
        record = _record_or_404(repository, project_id)
        audio = record.get("audio")
        if not isinstance(audio, dict):
            raise HTTPException(status_code=404, detail="El proyecto no tiene un audio asociado.")
        previous = copy.deepcopy(record)
        audio_path = None
        if isinstance(audio.get("filename"), str):
            try:
                audio_path = repository.media_path(project_id, audio["filename"])
            except ValueError as exc:
                raise HTTPException(status_code=500, detail="La ruta del audio guardado no es válida.") from exc
        rollback_path = None
        if audio_path is not None and audio_path.is_file():
            rollback_path = audio_path.with_name(f".audio-rollback-{uuid4().hex}")
            os.replace(audio_path, rollback_path)
        record["audio"] = None
        _invalidate_audio_transcription(record)
        try:
            await _persist_snapshot(repository, autosave, project_id, record)
        except BaseException:
            if rollback_path and rollback_path.exists() and audio_path is not None:
                os.replace(rollback_path, audio_path)
            await _restore_project_snapshot(repository, autosave, project_id, previous)
            raise
        if rollback_path:
            try:
                rollback_path.unlink(missing_ok=True)
            except OSError:
                pass
    return {"id": project_id, "deleted": True}


def _validate_transcription(payload: Any) -> tuple[dict[str, Any], TranscriptSummary]:
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise ValueError("Transcription JSON must contain a segments array")
    segment_count = len(payload["segments"])
    word_count = 0
    for index, segment in enumerate(payload["segments"]):
        if not isinstance(segment, dict):
            raise ValueError(f"Segment {index} must be an object")
        _validate_times(segment, f"segment {index}")
        if not isinstance(segment.get("text"), str):
            raise ValueError(f"Segment {index} needs text")
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError(f"Words in segment {index} must be an array")
        word_count += len(words)
        for word_index, word in enumerate(words):
            if not isinstance(word, dict):
                raise ValueError(f"Word {word_index} in segment {index} must be an object")
            _validate_times(word, f"word {word_index} in segment {index}")
            if not isinstance(word.get("text"), str):
                raise ValueError(f"Word {word_index} in segment {index} needs text")
    language = payload.get("language")
    if language is not None and not isinstance(language, str):
        raise ValueError("language must be a string")
    probability = payload.get("language_probability")
    if probability is not None:
        if not isinstance(probability, (float, int)) or not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("language_probability must be between 0 and 1")
    summary = TranscriptSummary(
        filename="",
        language=language,
        language_probability=probability,
        segment_count=segment_count,
        word_count=word_count,
    )
    return payload, summary


def _validate_times(item: dict[str, Any], label: str) -> None:
    start, end = item.get("start"), item.get("end")
    if (
        not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end < start
    ):
        raise ValueError(f"Invalid start/end timestamps in {label}")


@router.post("/{project_id}/transcription", response_model=TranscriptSummary, status_code=201)
async def import_transcription(
    project_id: str,
    request: Request,
    filename: str = Query("transcription.json", min_length=1, max_length=255),
) -> TranscriptSummary:
    repository, autosave = _services(request)
    _record_or_404(repository, project_id)
    body = await request.body()
    if not body or len(body) > MAX_TRANSCRIPT_BYTES:
        raise HTTPException(status_code=413 if body else 400, detail="Invalid transcription file size")
    try:
        payload, summary = _validate_transcription(json.loads(body.decode("utf-8-sig")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    safe_name = Path(filename.replace("\\", "/")).name
    if not safe_name.lower().endswith(".json") or safe_name in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="Transcription filename must end in .json")
    summary = summary.model_copy(update={"filename": safe_name})
    async with _project_lock(request, project_id):
        record = _record_or_404(repository, project_id)
        document = record.get("document")
        if isinstance(document, dict):
            document = dict(document)
            document.pop("phrase_transcription", None)
            record["document"] = document
        record["transcription"] = payload
        record["transcription_info"] = summary.model_dump()
        await _persist_snapshot(repository, autosave, project_id, record)
    return summary


def _parse_range(value: str, size: int) -> tuple[int, int] | None:
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or (not match.group(1) and not match.group(2)) or size <= 0:
        return None
    first, last = match.groups()
    if not first:
        suffix_length = int(last)
        if suffix_length <= 0:
            return None
        return max(0, size - suffix_length), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        return None
    return start, end


def _file_chunks(path: Path, start: int, length: int, chunk_size: int = 1024 * 1024) -> Any:
    with path.open("rb") as source:
        source.seek(start)
        remaining = length
        while remaining:
            chunk = source.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/{project_id}/media")
async def stream_media(project_id: str, request: Request) -> Response:
    repository, _ = _services(request)
    record = _record_or_404(repository, project_id)
    media = record.get("media")
    if not isinstance(media, dict) or not media.get("filename"):
        raise HTTPException(status_code=404, detail="Project has no imported video")
    try:
        path = repository.media_path(project_id, media["filename"])
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Stored media path is invalid") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Imported video file is missing")
    size = path.stat().st_size
    common = {
        "Accept-Ranges": "bytes",
        "Content-Type": "video/mp4",
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(media['filename'])}",
        "Cache-Control": "no-store",
    }
    range_header = request.headers.get("range")
    if range_header:
        parsed = _parse_range(range_header, size)
        if parsed is None:
            return Response(status_code=416, headers={**common, "Content-Range": f"bytes */{size}"})
        start, end = parsed
        return StreamingResponse(
            _file_chunks(path, start, end - start + 1),
            status_code=206,
            headers={**common, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"},
            media_type="video/mp4",
        )
    return StreamingResponse(
        _file_chunks(path, 0, size),
        headers={**common, "Content-Length": str(size)},
        media_type="video/mp4",
    )


@router.get("/{project_id}/audio")
async def stream_project_audio(project_id: str, request: Request) -> Response:
    repository, _ = _services(request)
    record = _record_or_404(repository, project_id)
    audio = record.get("audio")
    if not isinstance(audio, dict):
        media = record.get("media")
        if not isinstance(media, dict) or media.get("has_audio") is not True:
            raise HTTPException(status_code=404, detail="El proyecto no tiene audio asociado.")
        audio = media
    filename = audio.get("filename")
    if not isinstance(filename, str):
        raise HTTPException(status_code=404, detail="El proyecto no tiene audio asociado.")
    try:
        path = repository.media_path(project_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="La ruta del audio guardado no es válida.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="El archivo de audio importado no existe.")
    content_type = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".mp4": "video/mp4",
    }.get(path.suffix.lower())
    if content_type is None:
        raise HTTPException(status_code=415, detail="El formato de audio no es compatible.")
    size = path.stat().st_size
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": content_type,
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store",
    }
    range_header = request.headers.get("range")
    if range_header:
        parsed = _parse_range(range_header, size)
        if parsed is None:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        start, end = parsed
        return StreamingResponse(
            _file_chunks(path, start, end - start + 1), status_code=206,
            headers={**headers, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"},
            media_type=content_type,
        )
    return StreamingResponse(
        _file_chunks(path, 0, size), headers={**headers, "Content-Length": str(size)}, media_type=content_type,
    )
