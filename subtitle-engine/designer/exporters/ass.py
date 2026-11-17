"""ASS export with measured layout and timestamp-matched word karaoke layers."""

from pathlib import Path

from ..config import DesignerConfig
from ..fonts import ResolvedFont, resolve_font
from ..layout import validate_cues
from ..models import SubtitleCue, TimedWord
from .io import write_text


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, round(seconds * 100))
    hours, cs = divmod(centiseconds, 360_000)
    minutes, cs = divmod(cs, 6_000)
    whole_seconds, cs = divmod(cs, 100)
    return f"{hours}:{minutes:02}:{whole_seconds:02}.{cs:02}"


def _cs(seconds: float) -> int:
    return max(0, round(seconds * 100))


def _safe_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _render_lines(cue: SubtitleCue, active_index: int | None = None) -> str:
    lines: list[str] = []
    word_index = 0
    for line in cue.display_lines:
        pieces: list[str] = []
        for position, word in enumerate(line):
            token = word.text
            if position == 0:
                token = token.lstrip()
            if position == len(line) - 1:
                token = token.rstrip()
            token = _safe_text(token)
            if active_index is None:
                if word.highlighted:
                    pieces.append(r"{\b1}" + token + r"{\b0}")
                else:
                    pieces.append(token)
            elif word_index == active_index:
                duration_cs = max(1, _cs(word.end - word.start))
                pieces.append(r"{\alpha&H00&\kf" + str(duration_cs) + "}" + token)
            else:
                pieces.append(r"{\alpha&HFF&}" + token)
            word_index += 1
        lines.append("".join(pieces))
    return r"\N".join(lines)


def _position_tag(cue: SubtitleCue, config: DesignerConfig) -> str:
    x = round(config.play_res_x / 2)
    y = round(config.vertical_position * config.play_res_y)
    tag = rf"{{\an8\pos({x},{y})}}"
    if cue.font_size is not None:
        tag += rf"{{\fs{cue.font_size:g}}}"
    return tag


def _event(layer: int, cue: SubtitleCue, style: str, text: str,
           config: DesignerConfig, *, fade: bool = False,
           start: float | None = None, end: float | None = None) -> str:
    start = cue.start if start is None else start
    end = cue.end if end is None else end
    if end <= start:
        end = start + 0.01
    fade_tag = ""
    if fade and (config.fade_in_ms or config.fade_out_ms):
        fade_tag = "{\\fad(%d,%d)}" % (config.fade_in_ms, config.fade_out_ms)
    text = fade_tag + _position_tag(cue, config) + text
    return (
        f"Dialogue: {layer},{_ass_time(start)},{_ass_time(end)},{style},,"
        f"{config.margin_l},{config.margin_r},{config.margin_v},,{text}"
    )


def to_ass(
    cues: tuple[SubtitleCue, ...], config: DesignerConfig | None = None,
    *, validation_font: ResolvedFont | None = None,
) -> str:
    """Serialize centered cues; each karaoke overlay exists only for one word."""
    config = config or DesignerConfig()
    font = resolve_font(config.font_name, config.bold, config.fallback_font_names)
    # Keep font resolution for the ASS style independent from the face used by
    # geometric validation. Callers may provide a verified temporary face here;
    # its private GDI alias must never become the serialized ASS family name.
    validate_cues(
        cues, config,
        validation_font if validation_font is not None else font,
    )
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {config.play_res_x}
PlayResY: {config.play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,{font.family},{config.font_size:g},{config.primary_color},{config.active_color},{config.outline_color},{config.back_color},{-1 if config.bold else 0},0,0,0,100,100,0,0,1,{config.outline:g},{config.shadow:g},8,{config.margin_l},{config.margin_r},{config.margin_v},1
Style: Karaoke,{font.family},{config.font_size:g},{config.active_color},{config.primary_color},{config.outline_color},{config.back_color},{-1 if config.bold else 0},0,0,0,100,100,0,0,1,{config.outline:g},{config.shadow:g},8,{config.margin_l},{config.margin_r},{config.margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"""
    events: list[str] = []
    for cue in cues:
        if not cue.words:
            continue
        events.append(_event(0, cue, "Base", _render_lines(cue), config, fade=True))
        for index, word in enumerate(cue.words):
            if word.text.strip():
                events.append(
                    _event(
                        1, cue, "Karaoke", _render_lines(cue, index), config,
                        start=word.start, end=word.end,
                    )
                )
    return header + ("\n" + "\n".join(events) if events else "") + "\n"


def export_ass(
    cues: tuple[SubtitleCue, ...], destination: str | Path,
    config: DesignerConfig | None = None,
) -> Path:
    return write_text(to_ass(cues, config), destination)

