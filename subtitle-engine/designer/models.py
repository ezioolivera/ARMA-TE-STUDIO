"""Data models independent of transcription and export formats."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimedWord:
    text: str
    start: float
    end: float
    probability: float | None = None
    highlighted: bool = False
    source_segment: int = 0


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    start: float
    end: float
    words: tuple[TimedWord, ...]
    lines: tuple[tuple[TimedWord, ...], ...] = ()
    font_size: float | None = None

    @property
    def display_lines(self) -> tuple[tuple[TimedWord, ...], ...]:
        return self.lines or ((self.words,) if self.words else ())

    @property
    def text(self) -> str:
        return "\n".join("".join(word.text for word in line).strip() for line in self.display_lines)


@dataclass(frozen=True, slots=True)
class SubtitleDocument:
    language: str | None
    language_probability: float | None
    words: tuple[TimedWord, ...]

