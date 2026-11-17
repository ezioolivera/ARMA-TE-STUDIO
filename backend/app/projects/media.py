"""Video ingestion and FFprobe metadata extraction."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from app.core.config import settings
from app.projects.models import MediaInfo

MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024


class InvalidMedia(ValueError):
    pass


def safe_upload_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name
    if not name or name in {".", ".."} or Path(name).suffix.lower() != ".mp4":
        raise InvalidMedia("Only MP4 video files are supported")
    return name


async def copy_upload_to_file(request: Any, destination: Path) -> tuple[Path, int]:
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=destination.parent, prefix=f".{destination.name}.", suffix=".uploading", delete=False
    )
    temp_path = Path(handle.name)
    total = 0
    try:
        with handle as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_MEDIA_BYTES:
                    raise InvalidMedia("Video exceeds the 4 GiB upload limit")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if total == 0:
            raise InvalidMedia("Uploaded video is empty")
        return temp_path, total
    except BaseException:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _probe_sync(path: Path) -> dict[str, Any]:
    executable = shutil.which(settings.ffprobe_path)
    if executable is None:
        raise FileNotFoundError(
            f"FFprobe was not found ({settings.ffprobe_path}); configure ARMA_TE_FFPROBE"
        )
    result = subprocess.run(
        [executable, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        raise InvalidMedia(result.stderr.strip() or "FFprobe rejected this video")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InvalidMedia("FFprobe returned invalid metadata") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("streams"), list):
        raise InvalidMedia("File has no readable media streams")
    return payload


async def probe_video(path: Path, filename: str, relative_path: str) -> MediaInfo:
    payload = await asyncio.to_thread(_probe_sync, path)
    streams = payload["streams"]
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    if video is None:
        raise InvalidMedia("MP4 does not contain a video stream")
    duration_raw = video.get("duration") or payload.get("format", {}).get("duration")
    fps = _parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    return MediaInfo(
        filename=filename,
        relative_path=relative_path,
        duration=_parse_float(duration_raw),
        width=_parse_int(video.get("width")),
        height=_parse_int(video.get("height")),
        fps=fps,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        has_audio=audio is not None,
    )


def _parse_float(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _parse_rate(value: Any) -> float | None:
    if not isinstance(value, str) or "/" not in value:
        return _parse_float(value)
    numerator, denominator = value.split("/", 1)
    try:
        denominator_value = float(denominator)
        return float(numerator) / denominator_value if denominator_value else None
    except ValueError:
        return None
