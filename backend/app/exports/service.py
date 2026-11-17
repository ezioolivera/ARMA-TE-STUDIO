from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path
from uuid import uuid4

from app.projects.repository import ProjectRepository

from .adapters import export_ass_text, export_srt_text


class ExportService:
    def __init__(self, repository: ProjectRepository) -> None:
        self.repository = repository

    def next_output(self, project_id: str, project_name: str, suffix: str) -> Path:
        stem = re.sub(r"[^\w.-]+", "_", Path(project_name).stem, flags=re.UNICODE).strip("._") or "subtitles"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"{stem}_{stamp}_{uuid4().hex[:8]}.{suffix}"
        return self.repository.export_path(project_id, filename)

    def write_subtitle(self, project_id: str, record: dict, subtitle_format: str, layout: list[dict] | None = None) -> Path:
        path = self.next_output(project_id, record.get("name", "subtitles"), subtitle_format)
        if subtitle_format == "srt":
            content = export_srt_text(record)
        elif subtitle_format == "ass":
            content = export_ass_text(record, layout)
        else:
            raise ValueError("Formato de subtítulos no admitido.")
        temp = path.with_name(f".{path.stem}.{uuid4().hex}.tmp")
        try:
            temp.write_text(content, encoding="utf-8", newline="\n")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return path

