"""Backend adapter for the existing local Faster-Whisper engine."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ENGINE_ROOT = Path(__file__).resolve().parents[2] / "subtitle-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from transcription import (  # noqa: E402
    TranscriptionConfig,
    TranscriptionEngine,
    TranscriptionResult,
)


def _transcribe_sync(
    media_path: Path, config: TranscriptionConfig,
) -> TranscriptionResult:
    return TranscriptionEngine(config).transcribe(media_path)


async def transcribe_media(
    media_path: Path, config: TranscriptionConfig,
) -> TranscriptionResult:
    """Run the synchronous model inference on a worker thread."""
    return await asyncio.to_thread(_transcribe_sync, media_path, config)
