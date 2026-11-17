"""Public project and media data contracts."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MediaInfo(BaseModel):
    filename: str
    relative_path: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_audio: bool = False
    probe_error: str | None = None


class TranscriptSummary(BaseModel):
    filename: str
    language: str | None = None
    language_probability: float | None = None
    segment_count: int
    word_count: int


class ProjectDocument(BaseModel):
    model_config = ConfigDict(extra="allow")

    metadata: dict[str, Any] = Field(default_factory=dict)
    document: dict[str, Any] = Field(default_factory=dict)
    media: MediaInfo | None = None
    transcription: dict[str, Any] | None = None
    transcription_info: TranscriptSummary | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    media: MediaInfo | None = None
    transcription_info: TranscriptSummary | None = None


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    metadata: dict[str, Any] | None = None
    document: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Project name cannot be blank")
        return value
