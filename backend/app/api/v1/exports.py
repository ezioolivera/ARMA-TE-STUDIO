from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.exports.adapters import ExportValidationError, _ass_font_family, export_ass_text
from app.exports.async_process import AsyncSubprocess
from app.exports.fonts import FontPreparationError, materialize_bundled_font
from app.exports.service import ExportService
from app.jobs import InvalidJobTransition, JobManager, JobRecord, JobState, TaskRegistry
from app.projects.repository import ProjectNotFound, ProjectRepository

router = APIRouter(tags=["exports"])
logger = logging.getLogger(__name__)


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["srt", "ass", "mp4"]
    quality: Literal["high", "balanced", "compact"] = "balanced"
    layout: list[dict] | None = Field(default=None, max_length=100_000)
    diagnostics: dict | None = None


def _valid_diagnostic_id(value: str | None) -> str | None:
    if not value:
        return None


def _exception_message(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or f"{type(exc).__name__} (sin mensaje adicional)"
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _save_export_diagnostic(diagnostic_id: str, data: dict) -> Path:
    try:
        directory = Path(tempfile.gettempdir()) / "arma-te-export-diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{diagnostic_id}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8", newline="\n",
        )
        temporary.replace(destination)
        logger.info("Export diagnostic %s saved at %s", diagnostic_id, destination)
        return destination
    except Exception:
        logger.exception("Could not save optional export diagnostic %s", diagnostic_id)
        return Path()


def _services(request: Request) -> tuple[ProjectRepository, JobManager, ExportService]:
    repository = request.app.state.project_repository
    return repository, request.app.state.job_manager, ExportService(repository)


def _project(repository: ProjectRepository, project_id: str) -> dict:
    try:
        return repository.get(project_id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="No se encontró el proyecto.") from exc


def _job_payload(record: JobRecord, download_url: str | None = None) -> dict:
    return {
        "job_id": record.job_id,
        "job_type": record.job_type,
        "state": record.state.value,
        "progress": record.progress,
        "message": record.message,
        "cancel_requested": record.cancel_requested,
        "error": None if record.error is None else {
            "code": record.error.code, "message": record.error.message,
            "retryable": record.error.retryable,
        },
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "download_url": download_url,
    }


def _filter_path(path: Path) -> str:
    # The filter value is escaped for libavfilter's grammar; it is still passed
    # as one argv item with shell=False, never through a command shell.
    value = path.resolve().as_posix()
    for character in ("\\", "'", ":", ",", "[", "]"):
        value = value.replace(character, "\\" + character)
    return value


async def _verify_ass_font(
    ffmpeg: str, ass_path: Path, font_dir: Path, expected_family: str,
) -> str:
    """Ask this FFmpeg/libass build which face it resolves from the temporary TTF."""
    content = ass_path.read_text(encoding="utf-8")
    if "[Events]" not in content:
        raise FontPreparationError("El ASS no contiene una sección de eventos válida.")
    header = content.split("[Events]", 1)[0]
    probe_path = ass_path.with_name(f".{ass_path.stem}.font-probe.ass")
    probe_text = (
        header
        + "[Events]\n"
        + "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        + "Dialogue: 0,0:00:00.00,0:00:00.10,Base,,0,0,0,,ÁRBOL, NIÑO, ACCIÓN, ÚLTIMA.\n"
    )
    probe_path.write_text(probe_text, encoding="utf-8", newline="\n")
    subtitle_filter = (
        f"subtitles=filename='{_filter_path(probe_path)}':"
        f"fontsdir='{_filter_path(font_dir)}'"
    )
    process: AsyncSubprocess | None = None
    try:
        process = await AsyncSubprocess.create(
            ffmpeg, "-hide_banner", "-loglevel", "verbose",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=0.1",
            "-vf", subtitle_filter, "-frames:v", "1", "-f", "null", "-",
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            _, raw_stderr = await asyncio.wait_for(process.communicate(), timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
    finally:
        probe_path.unlink(missing_ok=True)

    stderr = raw_stderr.decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise FontPreparationError(
            "FFmpeg no pudo probar la fuente temporal de subtítulos: "
            + (stderr[-1800:] or f"código {process.returncode}.")
        )

    expected = re.sub(r"[^a-z0-9]", "", expected_family.casefold())
    selections = re.findall(
        r"fontselect:\s*\(([^,]+),\s*\d+,\s*\d+\)\s*->\s*([^,]+),\s*\d+,\s*([^\r\n]+)",
        stderr,
        flags=re.IGNORECASE,
    )
    for requested, selected, _postscript in selections:
        requested_key = re.sub(r"[^a-z0-9]", "", requested.casefold())
        selected_key = re.sub(r"[^a-z0-9]", "", selected.casefold())
        if requested_key == expected:
            if selected_key == expected or selected_key == expected + "regular":
                return selected.strip()
            raise FontPreparationError(
                f"FFmpeg/libass pidió {expected_family}, pero seleccionó {selected.strip()}."
            )
    raise FontPreparationError(
        f"FFmpeg/libass no confirmó la familia {expected_family}. "
        "La exportación se detuvo para evitar sustituirla por otra fuente."
    )


async def _render_mp4(
    request: Request, project_id: str, record: dict, quality: str, job_id: str,
    layout: list[dict] | None, diagnostic_id: str | None = None,
    frontend_diagnostics: dict | None = None,
) -> None:
    manager: JobManager = request.app.state.job_manager
    repository: ProjectRepository = request.app.state.project_repository
    task_registry: TaskRegistry = request.app.state.export_task_registry
    process: AsyncSubprocess | None = None
    partial: Path | None = None
    ass_path: Path | None = None
    font_temp: tempfile.TemporaryDirectory[str] | None = None
    verified_font: str | None = None
    diagnostic_record: dict | None = None
    if diagnostic_id:
        design = record.get("document", {}).get("design", {})
        diagnostic_record = {
            "diagnostic_id": diagnostic_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "job_id": job_id,
            "frontend": frontend_diagnostics,
            "project_design": {
                key: design.get(key) for key in (
                    "fontFamily", "fontSize", "maxWidth", "wordSpacing", "letterSpacing",
                    "outlineWidth", "shadow", "positionX", "positionY", "textFormat",
                )
            } if isinstance(design, dict) else {},
            "request_layout_cue1": layout[0] if layout else None,
            "backend_validation": {},
        }
        _save_export_diagnostic(diagnostic_id, diagnostic_record)
    try:
        manager.start(job_id, "Preparando subtítulos y video.")
        task_registry.begin_stage(job_id, "route_validation")
        media = record.get("media") if isinstance(record.get("media"), dict) else {}
        filename = media.get("filename")
        if not isinstance(filename, str) or not filename:
            raise ExportValidationError("Importa un video antes de exportar MP4.")
        try:
            input_path = repository.media_path(project_id, filename).resolve(strict=True)
        except (ValueError, OSError) as exc:
            raise ExportValidationError("No se encuentra el video importado del proyecto.") from exc
        project_dir = repository._directory(project_id)
        if input_path.parent != (project_dir / "media").resolve() or not input_path.is_file():
            raise ExportValidationError("La ruta del video del proyecto no es válida.")
        if not Path(settings.ffmpeg_path).is_file() and shutil.which(settings.ffmpeg_path) is None:
            raise ExportValidationError("FFmpeg no está disponible. Configura ARMA_TE_FFMPEG o instala FFmpeg.")
        task_registry.end_stage(job_id, "completed")
        task_registry.begin_stage(job_id, "input_output_preparation")
        output = ExportService(repository).next_output(project_id, record.get("name", "video"), "mp4")
        partial = output.with_name(f".{output.stem}.partial.mp4")
        ass_path = output.with_name(f".{output.stem}.ass")
        font_family = _ass_font_family(
            record.get("document", {}).get("design", {})
            if isinstance(record.get("document"), dict) else {},
        )
        task_registry.end_stage(job_id, "completed")
        task_registry.begin_stage(job_id, "font_materialization")
        font_temp = tempfile.TemporaryDirectory(prefix="arma-te-ass-fonts-")
        font_dir = Path(font_temp.name)
        font_path = materialize_bundled_font(font_family, font_dir)
        task_registry.end_stage(job_id, "completed")
        validation_capture: dict = {}
        try:
            ass_content = export_ass_text(
                record, layout,
                diagnostic_capture=validation_capture if diagnostic_record is not None else None,
                validation_font_path=font_path,
                stage_context=lambda name: task_registry.stage(job_id, name),
            )
        except Exception as exc:
            if diagnostic_record is not None:
                diagnostic_record["backend_validation"] = validation_capture
                diagnostic_record["outcome"] = "validation-failed"
                diagnostic_record["error"] = str(exc)
                _save_export_diagnostic(diagnostic_id, diagnostic_record)
            raise
        else:
            if diagnostic_record is not None:
                diagnostic_record["backend_validation"] = validation_capture
                diagnostic_record["outcome"] = "validation-passed"
                _save_export_diagnostic(diagnostic_id, diagnostic_record)
        with task_registry.stage(job_id, "ass_write"):
            ass_path.write_text(ass_content, encoding="utf-8", newline="\n")
        subtitle_filter = f"subtitles=filename='{_filter_path(ass_path)}'"
        if font_path is not None:
            with task_registry.stage(job_id, "font_probe_ffmpeg"):
                verified_font = await _verify_ass_font(settings.ffmpeg_path, ass_path, font_dir, font_family)
            manager.update_progress(job_id, 0, f"Fuente local verificada por libass: {verified_font}.")
            subtitle_filter += f":fontsdir='{_filter_path(font_dir)}'"
        ffmpeg = settings.ffmpeg_path
        crf = {"high": "18", "balanced": "23", "compact": "28"}[quality]
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats",
            "-i", str(input_path), "-map", "0:v:0", "-map", "0:a?",
            "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "medium", "-crf", crf,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-progress", "pipe:1", "-n", str(partial),
        ]
        task_registry.begin_stage(job_id, "ffmpeg_launch")
        process = await AsyncSubprocess.create(
            *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        task_registry.end_stage(job_id, "completed")
        stderr_task = asyncio.create_task(process.stderr.read())
        duration = float(media.get("duration") or 0)
        last_progress = 0.0
        task_registry.begin_stage(job_id, "ffmpeg_render")
        while True:
            if manager.is_cancel_requested(job_id):
                process.terminate()
                await process.wait()
                manager.acknowledge_cancel(job_id)
                task_registry.end_stage(job_id, "cancelled")
                task_registry.record_event(job_id, "job_finalization", "cancelled")
                return
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=.25)
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("out_time_us=") and duration > 0:
                try:
                    time_us = int(line.split("=", 1)[1])
                except ValueError:
                    continue
                progress = min(99.0, max(last_progress, time_us / (duration * 1_000_000) * 100))
                if progress > last_progress:
                    last_progress = progress
                    manager.update_progress(job_id, progress, f"Renderizando video: {progress:.1f}%")
        return_code = await process.wait()
        stderr = (await stderr_task).decode("utf-8", errors="replace")[-5000:]
        if return_code != 0:
            raise RuntimeError(stderr.strip() or f"FFmpeg terminó con código {return_code}.")
        task_registry.end_stage(job_id, "completed", return_code=return_code)
        task_registry.begin_stage(job_id, "job_finalization")
        os.replace(partial, output)
        request.app.state.job_exports[job_id] = (project_id, output.name)
        completion = "Video exportado."
        if verified_font:
            completion += f" Fuente verificada por libass: {verified_font}."
        manager.succeed(job_id, completion)
        task_registry.end_stage(job_id, "completed", job_state="succeeded")
    except asyncio.CancelledError as exc:
        task_registry.record_error(job_id, exc)
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        if manager.get(job_id) and manager.get(job_id).state == JobState.RUNNING:
            if manager.is_cancel_requested(job_id):
                manager.acknowledge_cancel(job_id)
            else:
                manager.request_cancel(job_id)
                manager.acknowledge_cancel(job_id)
        raise
    except ExportValidationError as exc:
        task_registry.record_error(job_id, exc)
        current = manager.get(job_id)
        if current and current.state in {JobState.RUNNING, JobState.QUEUED}:
            manager.fail(job_id, "export_validation", str(exc))
            task_registry.record_event(job_id, "job_finalization", "failed", error_type=type(exc).__name__, error=str(exc))
    except FontPreparationError as exc:
        task_registry.record_error(job_id, exc)
        current = manager.get(job_id)
        if current and current.state in {JobState.RUNNING, JobState.QUEUED}:
            manager.fail(job_id, "font_unavailable", str(exc))
            task_registry.record_event(job_id, "job_finalization", "failed", error_type=type(exc).__name__, error=str(exc))
    except Exception as exc:
        task_registry.record_error(job_id, exc)
        current = manager.get(job_id)
        if current and current.state in {JobState.RUNNING, JobState.QUEUED}:
            manager.fail(job_id, "render_failed", _exception_message(exc), retryable=True)
            task_registry.record_event(job_id, "job_finalization", "failed", error_type=type(exc).__name__, error=str(exc))
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        if partial:
            partial.unlink(missing_ok=True)
        if ass_path:
            ass_path.unlink(missing_ok=True)
        if font_temp:
            font_temp.cleanup()


async def _tracked_render_mp4(
    request: Request, project_id: str, record: dict, quality: str, job_id: str,
    layout: list[dict] | None, diagnostic_id: str | None,
    frontend_diagnostics: dict | None,
) -> None:
    registry: TaskRegistry = request.app.state.export_task_registry
    with registry.stage(job_id, "preparation_start"):
        registry.mark_running(job_id)
    await _render_mp4(
        request, project_id, record, quality, job_id, layout,
        diagnostic_id, frontend_diagnostics,
    )


def _export_task_done(job_id: str, task: asyncio.Task, request: Request) -> None:
    """Retrieve task result and reconcile a job that otherwise stayed active."""
    registry: TaskRegistry = request.app.state.export_task_registry
    manager: JobManager = request.app.state.job_manager
    exception = registry.finish(job_id, task)
    current = manager.get(job_id)

    if exception is not None:
        logger.error(
            "Unhandled export task exception job_id=%s",
            job_id,
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        if current and current.state in {JobState.QUEUED, JobState.RUNNING}:
            manager.fail(job_id, "export_task_exception", _exception_message(exception), retryable=True)
        registry.record_event(job_id, "job_reconciliation", manager.get(job_id).state.value)
        return

    if task.cancelled():
        if current and current.state == JobState.RUNNING and current.cancel_requested:
            manager.acknowledge_cancel(job_id)
        elif current and current.state in {JobState.QUEUED, JobState.RUNNING}:
            manager.fail(job_id, "export_task_cancelled", "La tarea se canceló sin una solicitud de cancelación válida.")
        registry.record_event(job_id, "job_reconciliation", manager.get(job_id).state.value)
        return

    if current and current.state in {JobState.QUEUED, JobState.RUNNING}:
        manager.fail(job_id, "export_task_ended", "La tarea terminó sin completar ni fallar el trabajo.", retryable=True)
        registry.record_event(job_id, "job_reconciliation", "failed")


@router.post("/projects/{project_id}/exports")
async def create_export(project_id: str, payload: ExportRequest, request: Request):
    repository, manager, service = _services(request)
    record = _project(repository, project_id)
    if payload.format in {"srt", "ass"}:
        try:
            path = await asyncio.to_thread(service.write_subtitle, project_id, record, payload.format, payload.layout)
        except (ExportValidationError, ValueError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return FileResponse(path, media_type="application/octet-stream", filename=path.name)
    media = record.get("media")
    if not isinstance(media, dict) or not media.get("filename"):
        raise HTTPException(status_code=422, detail="Importa un video antes de exportar MP4.")
    job = manager.create("video_export", "En cola para exportación.")
    diagnostic_id = _valid_diagnostic_id(request.headers.get("X-Export-Diagnostic-ID"))
    task = asyncio.create_task(_tracked_render_mp4(
        request, project_id, record, payload.quality, job.job_id, payload.layout,
        diagnostic_id, payload.diagnostics if diagnostic_id else None,
    ))
    request.app.state.export_tasks[job.job_id] = task
    request.app.state.export_task_registry.register(job.job_id, task)
    task.add_done_callback(lambda completed: _export_task_done(job.job_id, completed, request))
    task.add_done_callback(lambda _: request.app.state.export_tasks.pop(job.job_id, None))
    return JSONResponse(_job_payload(job), status_code=202)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    manager: JobManager = request.app.state.job_manager
    record = manager.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="No se encontró el trabajo.")
    result = request.app.state.job_exports.get(job_id)
    url = f"/api/v1/projects/{result[0]}/exports/{result[1]}" if result else None
    return _job_payload(record, url)


@router.get("/jobs/{job_id}/diagnostics")
async def get_job_diagnostics(job_id: str, request: Request, include_stack: bool = False):
    """Local-only execution diagnostics; stack frames omit locals and arguments."""
    client_host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=404, detail="No se encontrÃ³ el trabajo.")

    manager: JobManager = request.app.state.job_manager
    if manager.get(job_id) is None:
        raise HTTPException(status_code=404, detail="No se encontrÃ³ el trabajo.")
    registry: TaskRegistry = request.app.state.export_task_registry
    details = registry.get(job_id, include_stack=include_stack)
    if details is None:
        return {"job_id": job_id, "task_status": "not_registered", "current_stage": None, "duration_ms": None, "events": []}
    return details


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    manager: JobManager = request.app.state.job_manager
    try:
        result = manager.request_cancel(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No se encontró el trabajo.") from exc
    except InvalidJobTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_payload(result)


@router.get("/projects/{project_id}/exports/{filename}")
async def download_export(project_id: str, filename: str, request: Request):
    repository: ProjectRepository = request.app.state.project_repository
    _project(repository, project_id)
    if Path(filename).name != filename or Path(filename).suffix.lower() not in {".srt", ".ass", ".mp4"}:
        raise HTTPException(status_code=400, detail="El nombre de exportación no es válido.")
    try:
        path = repository.export_path(project_id, filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="El nombre de exportación no es válido.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No se encontró el archivo exportado.")
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)

