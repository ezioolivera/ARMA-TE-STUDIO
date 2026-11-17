"""FastAPI application entry point for the local Windows editor."""

from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import api_router
from app.core.config import settings
from app.jobs import JobManager, TaskRegistry
from app.persistence import AutosaveCoordinator, AutosavePolicy
from app.projects.repository import ProjectRepository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    data_dir = settings.project_data_dir
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    repository = ProjectRepository(data_dir)

    async def writer(project_id, snapshot):
        await asyncio.to_thread(repository.save, project_id, dict(snapshot))

    application.state.project_repository = repository
    application.state.project_locks = {}
    application.state.job_manager = JobManager()
    application.state.export_tasks = {}
    application.state.export_task_registry = TaskRegistry()
    application.state.job_exports = {}
    application.state.transcription_job_manager = JobManager()
    application.state.transcription_tasks = {}
    application.state.active_transcription_projects = {}
    application.state.autosave = AutosaveCoordinator(
        writer,
        AutosavePolicy(
            enabled=settings.autosave_enabled,
            debounce_seconds=settings.autosave_debounce_seconds,
            flush_on_shutdown=True,
        ),
    )
    try:
        yield
    finally:
        # Faster-Whisper runs in a worker thread and cannot be cancelled safely.
        # Let active jobs finish their project commit before closing autosave.
        transcription_tasks = tuple(application.state.transcription_tasks.values())
        if transcription_tasks:
            await asyncio.gather(*transcription_tasks, return_exceptions=True)
        tasks = tuple(application.state.export_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await application.state.autosave.close()
        except Exception:
            logger.exception("One or more project snapshots could not be saved during shutdown")

app = FastAPI(
    title="ARMA-TE Subtitle Editor API",
    version="0.1.0",
    description="Local API foundation for ARMA-TE Subtitle Editor.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Export-Diagnostic-ID"],
)

app.include_router(api_router, prefix="/api/v1")
