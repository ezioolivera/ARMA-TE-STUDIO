"""Project, media, transcript and byte-range playback endpoints."""

from __future__ import annotations

import json
import asyncio
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.persistence import AutosaveCoordinator
from app.projects.media import InvalidMedia, copy_upload_to_file, probe_video, safe_upload_name
from app.projects.models import (
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
