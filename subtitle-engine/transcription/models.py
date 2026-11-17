"""Stable, serializable transcript data structures."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    text: str
    start: float | None
    end: float | None
    probability: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    words: tuple[TranscriptWord, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    language: str | None
    language_probability: float | None
    segments: tuple[TranscriptSegment, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready dictionary including segment and word timing."""
        return asdict(self)
