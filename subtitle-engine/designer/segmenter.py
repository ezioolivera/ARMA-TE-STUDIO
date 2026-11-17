"""Pause-, phrase-, duration-, and measured-width-aware cue segmentation."""

import re

from .config import DesignerConfig
from .fonts import resolve_font
from .layout import balanced_lines, line_text, line_width_limit
from .metrics import TextMeasurer
from .models import SubtitleCue, SubtitleDocument, TimedWord

_SENTENCE_END = re.compile(r"[.!?…][\"'»”’)]*$")


def _ends_sentence(word: TimedWord) -> bool:
    return bool(_SENTENCE_END.search(word.text.strip()))


def _single_word_layout(
    word: TimedWord, measurer: TextMeasurer, config: DesignerConfig
) -> tuple[tuple[tuple[TimedWord, ...], ...], float]:
    limit = line_width_limit(config)
    for size in range(round(config.font_size), round(config.min_font_size) - 1, -1):
        width = measurer.measure(line_text([word]), size)
        if width <= limit:
            actual_size = float(size)
            return ((word,),), actual_size if actual_size != config.font_size else None
    raise ValueError(
        f"Word {word.text.strip()!r} cannot fit inside {config.max_width_ratio:.0%} "
        f"of the video width, even at {config.min_font_size:g}px"
    )


def _make_cue(
    words: list[TimedWord], lines: tuple[tuple[TimedWord, ...], ...],
    font_size: float | None = None,
) -> SubtitleCue:
    return SubtitleCue(
        start=words[0].start,
        end=words[-1].end,
        words=tuple(words),
        lines=lines,
        font_size=font_size,
    )


def segment_transcription(
    document: SubtitleDocument, config: DesignerConfig | None = None
) -> tuple[SubtitleCue, ...]:
    """Group words into centered one/two-line cues under the measured width cap.

    Pauses and sentence boundaries take precedence over the target word count.
    Candidate line breaks are selected by measured rendered width, balancing
    the two lines while keeping every word's source timestamps unchanged.
    """
    config = config or DesignerConfig()
    font = resolve_font(config.font_name, config.bold, config.fallback_font_names)
    cues: list[SubtitleCue] = []
    current: list[TimedWord] = []

    with TextMeasurer(font, config.font_size, config.bold) as measurer:
        def flush() -> None:
            nonlocal current
            if not current:
                return
            lines = balanced_lines(current, measurer, config)
            size = None
            if lines is None:
                if len(current) != 1:
                    raise ValueError("Internal layout error: cue is wider than two lines")
                lines, size = _single_word_layout(current[0], measurer, config)
            cues.append(_make_cue(current, lines, size))
            current = []

        for item in document.words:
            if not item.text.strip():
                continue
            if current:
                previous = current[-1]
                pause = max(0.0, item.start - previous.end)
                if pause >= config.pause_threshold or _ends_sentence(previous):
                    flush()

            candidate = current + [item]
            duration_exceeded = bool(current) and item.end - current[0].start > config.max_duration
            count_exceeded = len(candidate) > config.max_words
            chars_exceeded = len("".join(word.text for word in candidate).strip()) > config.max_chars
            width_exceeded = balanced_lines(candidate, measurer, config) is None

            if current and (duration_exceeded or count_exceeded or chars_exceeded or width_exceeded):
                flush()
                candidate = [item]
                width_exceeded = balanced_lines(candidate, measurer, config) is None

            current.append(item)
            if width_exceeded:
                flush()

        flush()

    return tuple(cues)


def segment_words(
    words: tuple[TimedWord, ...], config: DesignerConfig | None = None
) -> tuple[SubtitleCue, ...]:
    """Convenience entry point for a flat word list."""
    return segment_transcription(SubtitleDocument(None, None, words), config)

