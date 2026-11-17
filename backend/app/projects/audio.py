"""Audio ingestion and FFprobe metadata extraction."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.projects.media import InvalidMedia, _parse_float, _parse_int, _probe_sync
from app.projects.models import AudioInfo


def safe_audio_upload_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    if not name or name in {".", ".."} or Path(name).suffix.lower() not in {".wav", ".mp3"}:
        raise InvalidMedia("Solo se admiten archivos WAV o MP3")
    return name


def _probe_audio_sync(path: Path, expected_suffix: str) -> dict[str, Any]:
    payload = _probe_sync(path)
    streams = payload["streams"]
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if audio is None:
        raise InvalidMedia("El archivo no contiene una pista de audio")
    format_data = payload.get("format", {})
    format_names = {item.strip().lower() for item in str(format_data.get("format_name", "")).split(",")}
    suffix = expected_suffix.lower()
    if (suffix == ".wav" and not (format_names & {"wav", "wavx"})) or (
        suffix == ".mp3" and "mp3" not in format_names
    ):
        raise InvalidMedia("La extensión del archivo no coincide con su formato de audio real")
    duration = _parse_float(audio.get("duration"))
    if duration is None:
        duration = _parse_float(format_data.get("duration"))
    if duration is None or duration <= 0:
        raise InvalidMedia("No se pudo obtener una duración válida del audio")
    return {
        "duration": duration,
        "sample_rate": _parse_int(audio.get("sample_rate")),
        "channels": _parse_int(audio.get("channels")),
        "codec": audio.get("codec_name"),
        "format_name": format_data.get("format_name"),
    }


async def probe_audio(path: Path, filename: str, relative_path: str) -> AudioInfo:
    metadata = await asyncio.to_thread(_probe_audio_sync, path, Path(filename).suffix)
    return AudioInfo(filename=filename, relative_path=relative_path, **metadata)
