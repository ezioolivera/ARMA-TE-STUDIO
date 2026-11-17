"""Crash-safe JSON project repository with per-project media directories."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import UUID, uuid4


class ProjectNotFound(LookupError):
    pass


class ProjectRepository:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, project_id: str) -> Path:
        try:
            safe_id = str(UUID(project_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ProjectNotFound(project_id) from exc
        path = (self.root / safe_id).resolve()
        if path.parent != self.root:
            raise ProjectNotFound(project_id)
        return path

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def create(self, name: str) -> dict[str, Any]:
        project_id = str(uuid4())
        now = self._now()
        directory = self._directory(project_id)
        (directory / "media").mkdir(parents=True)
        record: dict[str, Any] = {
            "id": project_id,
            "name": name.strip(),
            "created_at": now,
            "updated_at": now,
            "metadata": {},
            "document": {},
            "media": None,
            "transcription": None,
            "transcription_info": None,
        }
        self.save(project_id, record)
        return record

    def get(self, project_id: str) -> dict[str, Any]:
        path = self._directory(project_id) / "project.json"
        if not path.is_file():
            raise ProjectNotFound(project_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Project file is unreadable: {project_id}") from exc
        if not isinstance(record, dict) or record.get("id") != project_id:
            raise RuntimeError(f"Project file is invalid: {project_id}")
        return record

    def delete(self, project_id: str) -> None:
        """Remove only the UUID-scoped project directory under this repository root."""
        directory = self._directory(project_id)
        if not directory.is_dir():
            raise ProjectNotFound(project_id)
        try:
            shutil.rmtree(directory)
        except FileNotFoundError as exc:
            raise ProjectNotFound(project_id) from exc

    def list(self) -> list[dict[str, Any]]:
        records = []
        for project_dir in self.root.iterdir():
            if not project_dir.is_dir():
                continue
            try:
                records.append(self.get(project_dir.name))
            except ProjectNotFound:
                continue
        records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return records

    def save(self, project_id: str, record: dict[str, Any]) -> None:
        directory = self._directory(project_id)
        directory.mkdir(parents=True, exist_ok=True)
        record["updated_at"] = self._now()
        destination = directory / "project.json"
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=directory, prefix=".project-", suffix=".tmp", delete=False
            ) as temp:
                temp_name = temp.name
                json.dump(record, temp, ensure_ascii=False, indent=2, allow_nan=False)
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_name, destination)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    def merge_update(self, project_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        record = self.get(project_id)
        for key, value in changes.items():
            if value is not None:
                record[key] = value
        self.save(project_id, record)
        return record

    def media_path(self, project_id: str, filename: str) -> Path:
        safe_name = Path(filename).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError("Invalid media filename")
        directory = self._directory(project_id) / "media"
        directory.mkdir(parents=True, exist_ok=True)
        path = (directory / safe_name).resolve()
        if path.parent != directory.resolve():
            raise ValueError("Invalid media filename")
        return path

    def exports_directory(self, project_id: str) -> Path:
        """Return the project-scoped export directory after validating its UUID root."""
        directory = self._directory(project_id) / "exports"
        directory.mkdir(parents=True, exist_ok=True)
        if directory.resolve().parent != self._directory(project_id):
            raise ValueError("Invalid project export path")
        return directory

    def export_path(self, project_id: str, filename: str) -> Path:
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise ValueError("Invalid export filename")
        directory = self.exports_directory(project_id).resolve()
        path = (directory / filename).resolve()
        if path.parent != directory:
            raise ValueError("Invalid project export path")
        return path
