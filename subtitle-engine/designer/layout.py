"""Measured line layout and safety validation shared by segmenter/exporters."""

from __future__ import annotations

from .config import DesignerConfig
from .fonts import ResolvedFont, resolve_font
from .metrics import TextMeasurer
from .models import SubtitleCue, TimedWord


def line_text(words: tuple[TimedWord, ...] | list[TimedWord]) -> str:
    return "".join(word.text for word in words).strip()


def line_width_limit(config: DesignerConfig) -> float:
    # Leave room for the ASS outline and shadow within the 85% content limit.
    decoration = 2 * (config.outline + config.shadow)
    return config.play_res_x * config.max_width_ratio - decoration


def balanced_lines(
    words: list[TimedWord], measurer: TextMeasurer, config: DesignerConfig
) -> tuple[tuple[TimedWord, ...], ...] | None:
    """Return one line or the most balanced legal two-line word split."""
    limit = line_width_limit(config)
    if measurer.measure(line_text(words)) <= limit:
        return (tuple(words),)
    if len(words) < 2:
        return None

    candidates: list[tuple[float, tuple[TimedWord, ...], tuple[TimedWord, ...]]] = []
    for split in range(1, len(words)):
        first, second = tuple(words[:split]), tuple(words[split:])
        width1 = measurer.measure(line_text(first))
        width2 = measurer.measure(line_text(second))
        if width1 <= limit and width2 <= limit:
            candidates.append((abs(width1 - width2), first, second))
    if not candidates:
        return None
    _, first, second = min(candidates, key=lambda candidate: candidate[0])
    return first, second


def _effective_font_size(cue: SubtitleCue, config: DesignerConfig) -> float:
    return cue.font_size or config.font_size


def validate_position(cue: SubtitleCue, config: DesignerConfig) -> None:
    line_count = len(cue.display_lines)
    size = _effective_font_size(cue, config)
    top = config.vertical_position * config.play_res_y
    bottom = top + line_count * size * 1.22 + 2 * config.outline
    safe_top = config.safe_top_ratio * config.play_res_y
    safe_bottom = (1 - config.safe_bottom_ratio) * config.play_res_y
    if top < safe_top or bottom > safe_bottom:
        raise ValueError("Subtitle block falls outside the configured vertical safe area")

    if config.watermark_box is None:
        return
    left_ratio, top_ratio, right_ratio, bottom_ratio = config.watermark_box
    left = (1 - config.max_width_ratio) * config.play_res_x / 2
    right = config.play_res_x - left
    wm_left, wm_top = left_ratio * config.play_res_x, top_ratio * config.play_res_y
    wm_right, wm_bottom = right_ratio * config.play_res_x, bottom_ratio * config.play_res_y
    if left < wm_right and right > wm_left and top < wm_bottom and bottom > wm_top:
        raise ValueError("Subtitle block overlaps the configured watermark safe box")


def validate_cues(
    cues: tuple[SubtitleCue, ...],
    config: DesignerConfig,
    font: ResolvedFont | None = None,
) -> None:
    """Raise when a cue exceeds width/line limits or intrudes into unsafe area."""
    font = font or resolve_font(config.font_name, config.bold, config.fallback_font_names)
    with TextMeasurer(font, config.font_size, config.bold) as measurer:
        limit = line_width_limit(config)
        for cue_index, cue in enumerate(cues, start=1):
            if len(cue.display_lines) > config.max_lines:
                raise ValueError(f"Cue {cue_index} has more than {config.max_lines} lines")
            size = _effective_font_size(cue, config)
            for line_index, words in enumerate(cue.display_lines, start=1):
                width = measurer.measure(line_text(words), size) + 2 * (config.outline + config.shadow)
                if width > config.play_res_x * config.max_width_ratio + 0.01:
                    raise ValueError(
                        f"Cue {cue_index}, line {line_index} is {width:.1f}px; "
                        f"limit is {config.play_res_x * config.max_width_ratio:.1f}px"
                    )
            validate_position(cue, config)

