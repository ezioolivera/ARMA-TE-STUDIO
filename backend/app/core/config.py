"""Environment-backed settings; paths are resolved for Windows by default."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "ARMA-TE Subtitle Editor API"
    cors_origins: tuple[str, ...] = (
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    autosave_enabled: bool = True
    autosave_debounce_seconds: float = 1.0
    project_data_dir: Path = Path("input") / "projects"
    ffprobe_path: str = "ffprobe"
    ffmpeg_path: str = "ffmpeg"


def _settings_from_environment() -> Settings:
    defaults = Settings()
    cors_value = os.getenv("ARMA_TE_CORS_ORIGINS")
    origins = (
        tuple(origin.strip() for origin in cors_value.split(",") if origin.strip())
        if cors_value
        else defaults.cors_origins
    )
    project_dir = Path(os.getenv("ARMA_TE_PROJECT_DATA_DIR", str(defaults.project_data_dir)))
    return Settings(
        cors_origins=origins,
        autosave_enabled=os.getenv("ARMA_TE_AUTOSAVE_ENABLED", "true").lower() == "true",
        autosave_debounce_seconds=float(os.getenv("ARMA_TE_AUTOSAVE_DEBOUNCE", "1.0")),
        project_data_dir=project_dir,
        ffprobe_path=os.getenv("ARMA_TE_FFPROBE", "ffprobe"),
        ffmpeg_path=os.getenv("ARMA_TE_FFMPEG", "ffmpeg"),
    )


settings = _settings_from_environment()
