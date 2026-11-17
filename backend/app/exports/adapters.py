from __future__ import annotations

from dataclasses import fields, replace
from contextlib import ExitStack, contextmanager, nullcontext
import tempfile
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, ContextManager, Iterator

ENGINE_ROOT = Path(__file__).resolve().parents[3] / "subtitle-engine"
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from designer import (  # noqa: E402
    DesignerConfig, ResolvedFont, SubtitleCue, SubtitleDocument, TimedWord,
    resolve_font, segment_transcription, to_ass, to_srt, validate_cues,
)
from designer.metrics import TextMeasurer  # noqa: E402
from app.exports.fonts import (
    FontPreparationError, ass_font_layout_metrics, ass_font_size_scale,
    materialize_bundled_font, validation_font,
)


class ExportValidationError(ValueError):
    pass


_ASS_LAYOUT_WIDTH_TOLERANCE_PX = 0.25
_DESIGNER_WIDTH_EPSILON_PX = 0.01

_BUNDLED_FONT_WEIGHT = {
    "Anton": 400,
    "Bebas Neue": 400,
    "Montserrat ExtraBold": 800,
    "Montserrat Black": 900,
}


@contextmanager
def _selected_validation_font(
    design: dict[str, Any], font_path: Path | None,
) -> Iterator[tuple[ResolvedFont | None, Any | None]]:
    """Keep the exact selected face registered through both ASS validations."""
    family = str(design.get("fontFamily", "Montserrat ExtraBold"))
    expected_weight = _BUNDLED_FONT_WEIGHT.get(family)
    if expected_weight is None:
        yield None, None
        return

    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        selected_path = font_path
        if selected_path is None:
            temporary = tempfile.TemporaryDirectory(prefix="arma-te-exact-font-")
            selected_path = materialize_bundled_font(family, Path(temporary.name))
        if selected_path is None:
            raise ExportValidationError(
                f"No se pudo preparar el TTF local exacto de {family} para validar la exportación."
            )
        with ExitStack() as stack:
            try:
                resolved_path, resolved_family, identity = stack.enter_context(
                    validation_font(selected_path, family, expected_weight)
                )
            except FontPreparationError as exc:
                raise ExportValidationError(
                    f"No se pudo garantizar la fuente exacta {family} durante la validación: {exc}"
                ) from exc
            resolved = ResolvedFont(resolved_family, resolved_path, family)
            # Yield inside validation_font() so its GDI alias and TTF remain
            # available for both geometric_validation and ass_generation.
            yield resolved, identity
    finally:
        if temporary is not None:
            temporary.cleanup()


def _validate_with_selected_font(
    cues: list[tuple[SubtitleCue, int]], config: DesignerConfig,
    design: dict[str, Any], resolved: ResolvedFont | None, identity: Any | None,
    diagnostic_capture: dict[str, Any] | None,
) -> None:
    """Run the geometric guard against the exact bundled TTF selected in design."""
    if resolved is None or identity is None:
        return
    family = str(design.get("fontFamily", "Montserrat ExtraBold"))
    expected_weight = _BUNDLED_FONT_WEIGHT[family]
    try:
        # Preserve the existing geometric limit and 0.01 px epsilon.
        validate_cues(tuple(cue for cue, _ in cues), config, resolved)
    except Exception as exc:
        if diagnostic_capture is not None:
            diagnostic_capture["exact_font_validation_error"] = str(exc)
        raise ExportValidationError(
            f"La validación geométrica con {family} ({identity.metric_backend}) falló: {exc}"
        ) from exc

    if diagnostic_capture is not None:
        diagnostic_capture["exact_font_validation"] = {
            "requested_family": family,
            "requested_variant": {
                "Anton": "Regular 400",
                "Bebas Neue": "Regular 400",
                "Montserrat ExtraBold": "ExtraBold 800",
                "Montserrat Black": "Black 900",
            }[family],
            "requested_weight_class": expected_weight,
            "resolved_family": identity.resolved_family,
            "resolved_path": str(identity.resolved_path),
            "source_ttf_path": str(identity.source_path),
            "source_ttf_sha256": identity.source_sha256,
            "registered_ttf_sha256": identity.registered_sha256,
            "actual_weight_class": identity.weight_class,
            "metric_backend": identity.metric_backend,
            "cue_count": len(cues),
            "max_width_ratio": config.max_width_ratio,
            "geometric_limit_px": config.play_res_x * config.max_width_ratio,
            "outline_px": config.outline,
            "shadow_px": config.shadow,
            "ass_serializer_font_injection": "validation-only ResolvedFont reused",
        }


def _ass_layout_validation_config(config: DesignerConfig) -> SimpleNamespace:
    """Give Designer's secondary font check a 0.25 px measurement tolerance.

    The editor manifest was measured in Chromium with its bundled local face;
    Designer remeasures it using Windows font metrics. Keep this allowance in
    a validation-only view of the config. ASS serialization fields and the
    original project design remain unchanged.
    """
    values = {field.name: getattr(config, field.name) for field in fields(config)}
    validation_only_pixels = max(
        0.0, _ASS_LAYOUT_WIDTH_TOLERANCE_PX - _DESIGNER_WIDTH_EPSILON_PX,
    )
    values["max_width_ratio"] = (
        config.max_width_ratio + validation_only_pixels / config.play_res_x
    )
    return SimpleNamespace(**values)


def _transcription(record: dict[str, Any]) -> dict[str, Any]:
    document = record.get("document")
    source = document.get("transcription") if isinstance(document, dict) else None
    if not isinstance(source, dict):
        source = record.get("transcription")
    if not isinstance(source, dict) or not isinstance(source.get("segments"), list):
        raise ExportValidationError("El proyecto no contiene una transcripción válida.")
    return source


def _format_text(text: str, mode: str) -> str:
    if mode == "uppercase":
        return text.upper()
    if mode == "lowercase":
        return text.lower()
    if mode == "title":
        return re.sub(r"[^\W_][\w\u0300-\u036f'’-]*", lambda match: match.group(0)[:1].upper() + match.group(0)[1:].lower(), text, flags=re.UNICODE)
    return text


def _design(record: dict[str, Any]) -> dict[str, Any]:
    document = record.get("document")
    design = document.get("design") if isinstance(document, dict) else None
    return design if isinstance(design, dict) else {}


def _segments(record: dict[str, Any], *, formatted: bool) -> list[tuple[float, float, str, list[TimedWord]]]:
    source = _transcription(record)
    text_format = str(_design(record).get("textFormat", "original")) if formatted else "original"
    output: list[tuple[float, float, str, list[TimedWord]]] = []
    for index, raw in enumerate(source["segments"]):
        if not isinstance(raw, dict):
            raise ExportValidationError(f"El segmento {index + 1} no es un objeto válido.")
        try:
            start, end = float(raw["start"]), float(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportValidationError(f"El segmento {index + 1} no tiene tiempos válidos.") from exc
        if start < 0 or end <= start:
            raise ExportValidationError(f"El intervalo del segmento {index + 1} no es válido.")
        text = raw.get("text")
        if not isinstance(text, str):
            raise ExportValidationError(f"El segmento {index + 1} no tiene texto.")
        words: list[TimedWord] = []
        for word_index, word in enumerate(raw.get("words", [])):
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise ExportValidationError(f"La palabra {word_index + 1} del segmento {index + 1} no es válida.")
            try:
                word_start, word_end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ExportValidationError(f"La palabra {word_index + 1} del segmento {index + 1} no tiene tiempos válidos.") from exc
            if word_start < 0 or word_end <= word_start:
                raise ExportValidationError(f"El intervalo de la palabra {word_index + 1} del segmento {index + 1} no es válido.")
            words.append(TimedWord(
                _format_text(word["text"], text_format), word_start, word_end,
                probability=word.get("probability"), source_segment=index,
            ))
        if formatted:
            text = _format_text(text, text_format)
        output.append((start, end, text, words))
    return output


def export_srt_text(record: dict[str, Any]) -> str:
    """Use the existing Designer SRT serializer, preserving the editor's cue boundaries."""
    cues = tuple(
        SubtitleCue(start, end, (TimedWord(text, start, end, source_segment=index),))
        for index, (start, end, text, _) in enumerate(_segments(record, formatted=True))
        if text.strip()
    )
    return to_srt(cues)


def _ass_color(value: object, fallback: str) -> str:
    candidate = str(value)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", candidate):
        candidate = fallback
    red, green, blue = candidate[1:3], candidate[3:5], candidate[5:7]
    return f"&H00{blue.upper()}{green.upper()}{red.upper()}"


def _font_name(design: dict[str, Any]) -> str:
    requested = str(design.get("fontFamily", "Montserrat ExtraBold"))
    if requested in {"Montserrat ExtraBold", "Montserrat Black"}:
        family, fallbacks = "Montserrat", ("Arial Narrow", "Arial")
    elif requested == "Anton":
        family, fallbacks = "Anton", ("Arial Narrow", "Arial")
    elif requested == "Bebas Neue":
        family, fallbacks = "Bebas Neue", ("Arial Narrow", "Arial")
    else:
        family, fallbacks = "Arial", ("Arial Narrow",)
    return resolve_font(family, True, fallbacks).family


def _ass_font_family(design: dict[str, Any]) -> str:
    requested = str(design.get("fontFamily", "Montserrat ExtraBold"))
    if requested in {"Montserrat ExtraBold", "Montserrat Black"}:
        return requested
    if requested == "Anton":
        return "Anton"
    if requested == "Bebas Neue":
        return "Bebas Neue"
    return "Arial"


def _base_config(
    record: dict[str, Any], width: int, height: int, render_scale: float = 1.0,
) -> DesignerConfig:
    design = _design(record)
    requested_font = str(design.get("fontFamily", "Montserrat ExtraBold"))
    width, height = max(1, width), max(1, height)
    font_size = max(10, width * float(design.get("fontSize", 56)) / 1080)
    return DesignerConfig.for_video(
        width, height,
        font_name=_font_name(design),
        fallback_font_names=("Arial Narrow", "Arial"),
        font_size=font_size,
        min_font_size=10,
        bold=requested_font not in {"Anton", "Bebas Neue"},
        primary_color=_ass_color(design.get("textColor"), "#ffffff"),
        active_color=_ass_color(design.get("activeWordColor"), "#37d7d5"),
        outline_color=_ass_color(design.get("outlineColor"), "#000000"),
        outline=float(design.get("outlineWidth", 2)) * render_scale,
        shadow=2 * render_scale if design.get("shadow", True) else 0,
        vertical_position=min(.96, max(.04, float(design.get("positionY", 62)) / 100)),
        watermark_box=None,
        max_width_ratio=min(.85, max(.3, float(design.get("maxWidth", 85)) / 100)),
        fade_in_ms=0,
        fade_out_ms=0,
    )


def _ass_time(seconds: float) -> str:
    value = max(0, round(seconds * 100))
    hours, value = divmod(value, 360_000)
    minutes, value = divmod(value, 6_000)
    whole, centiseconds = divmod(value, 100)
    return f"{hours}:{minutes:02}:{whole:02}.{centiseconds:02}"


_ASS_TIME = re.compile(r"^(\d+):(\d{2}):(\d{2})\.(\d{2})$")


def _seconds(value: str) -> float:
    match = _ASS_TIME.match(value)
    if not match:
        raise ExportValidationError("El motor generó un timestamp ASS no válido.")
    hours, minutes, seconds, centiseconds = map(int, match.groups())
    return hours * 3600 + minutes * 60 + seconds + centiseconds / 100


def _active_design(base: dict[str, Any], time: float) -> dict[str, Any]:
    events = base.get("temporalEvents", [])
    active = [event for event in events if isinstance(event, dict) and float(event.get("start", 0)) <= time < float(event.get("end", 0))]
    if not active:
        return base
    event = max(active, key=lambda item: float(item.get("start", 0)))
    resolved = dict(base)
    if isinstance(event.get("overrides"), dict):
        resolved.update(event["overrides"])
    if event.get("position") == "base":
        return resolved
    resolved["positionX"] = float(event.get("positionX", 50))
    position = event.get("position")
    resolved["positionY"] = {"top": 20, "center": 50, "bottom": 82}.get(position, float(event.get("positionY", 62)))
    return resolved


def _position_tag(design: dict[str, Any], config: DesignerConfig, y_offset: int = 0) -> str:
    max_width = min(.85, max(.3, float(design.get("maxWidth", 85)) / 100))
    x_percent = float(design.get("positionX", 50)) / 100 * (1 - max_width)
    alignment = design.get("alignment", "center")
    if alignment == "left":
        x_percent += 0
        an = 4
    elif alignment == "right":
        x_percent += max_width
        an = 6
    else:
        x_percent += max_width / 2
        an = 5
    # CSS centers the subtitle block vertically; ASS uses the middle-row anchor.
    y = min(.96, max(.04, float(design.get("positionY", 62)) / 100))
    return rf"{{\an{an}\pos({round(x_percent * config.play_res_x)},{round(y * config.play_res_y) + y_offset})}}"


def _apply_temporal_positions(ass: str, design: dict[str, Any], config: DesignerConfig) -> str:
    output: list[str] = []
    position_re = re.compile(r"\{\\an\d+\\pos\(\d+,\d+\)\}")
    for line in ass.splitlines():
        if not line.startswith("Dialogue:"):
            output.append(line)
            continue
        fields = line.split(",", 9)
        if len(fields) != 10:
            output.append(line)
            continue
        start, end = _seconds(fields[1]), _seconds(fields[2])
        original_position = re.search(r"\\pos\((\d+),(\d+)\)", fields[9])
        default_y = round(float(design.get("positionY", 62)) / 100 * config.play_res_y)
        y_offset = int(original_position.group(2)) - default_y if original_position else 0
        boundaries = sorted({start, end, *(
            point for event in design.get("temporalEvents", []) if isinstance(event, dict)
            for point in (float(event.get("start", start)), float(event.get("end", end)))
            if start < point < end
        )})
        for left, right in zip(boundaries, boundaries[1:]):
            if right <= left:
                continue
            active = _active_design(design, (left + right) / 2)
            text = position_re.sub(lambda _: _position_tag(active, config, y_offset), fields[9], count=1)
            layer = int(fields[0].split(":", 1)[1])
            if layer == 1 and len(boundaries) > 2:
                text = re.sub(r"\\kf\d+", rf"\\kf{max(1, round((right - left) * 100))}", text, count=1)
            output.append(",".join((fields[0], _ass_time(left), _ass_time(right), *fields[3:9], text)))
    return "\n".join(output) + "\n"


def _align_ass_rendering_to_preview(
    ass: str, design: dict[str, Any], font_family: str, font_scale: float,
) -> str:
    """Match CSS word gaps and line-box baselines using the bundled face metrics."""
    metrics = ass_font_layout_metrics(font_family)
    if metrics is None:
        return ass
    space_advance_per_em, baseline_shift_per_em = metrics
    style_sizes: dict[str, float] = {}
    style_spacing: dict[str, float] = {}
    output: list[str] = []
    for line in ass.splitlines():
        if line.startswith("Style: "):
            fields = line.split(",")
            if len(fields) > 13:
                style_name = fields[0].removeprefix("Style: ")
                try:
                    style_sizes[style_name] = float(fields[2])
                    style_spacing[style_name] = float(fields[13])
                except ValueError:
                    pass
            output.append(line)
            continue
        if not line.startswith("Dialogue:"):
            output.append(line)
            continue

        fields = line.split(",", 9)
        if len(fields) != 10:
            output.append(line)
            continue
        style_name = fields[3]
        style_size = style_sizes.get(style_name)
        inline_size = re.search(r"\\fs(\d+(?:\.\d+)?)", fields[9])
        ass_font_size = float(inline_size.group(1)) if inline_size else style_size
        if ass_font_size is None or font_scale <= 0:
            output.append(line)
            continue

        css_font_size = ass_font_size / font_scale
        letter_spacing = style_spacing.get(style_name, 0.0)
        # \\fsp uses PlayRes units. The ASS font size has been raised by
        # ``font_scale`` to match libass's Windows line-height interpretation,
        # but that correction must not also enlarge the native space advance
        # when matching the browser's CSS column-gap. Compute the natural
        # separator advance in the original CSS/source-pixel coordinate space,
        # then apply only the difference as \\fsp.
        requested_gap = css_font_size * float(design.get("wordSpacing", 0.16))
        native_space = css_font_size * space_advance_per_em
        # Preserve the preceding glyph's style spacing, size the separator's
        # trailing advance to the CSS gap, then restore tracking for word text.
        separator_spacing = requested_gap - native_space - letter_spacing
        text = re.sub(
            r"(?<=\S) +(?=\S)",
            lambda match: (
                "{\\fsp" + f"{separator_spacing:.4f}" + "}" + match.group(0)
                + "{\\fsp" + f"{letter_spacing:.4f}" + "}"
            ),
            fields[9],
        )
        baseline_shift = round(baseline_shift_per_em * css_font_size)
        if baseline_shift:
            text = re.sub(
                r"(\\pos\((\d+),)(\d+)(\)\})",
                lambda match: (
                    match.group(1) + str(int(match.group(3)) + baseline_shift) + match.group(4)
                ),
                text,
                count=1,
            )
        fields[9] = text
        output.append(",".join(fields))
    return "\n".join(output) + "\n"


def _add_independent_shadow_layer(ass: str, shadow_offset: float) -> str:
    """Add a blurred text-mask beneath each base event without softening its face."""
    if shadow_offset <= 0:
        return ass

    shadow_marker = "ARMA-TE-SHADOW"
    lines = ass.splitlines()
    if any(
        line.startswith("Dialogue:")
        and len(line.split(",", 9)) == 10
        and line.split(",", 9)[4] == shadow_marker
        for line in lines
    ):
        return ass

    blur_radius = shadow_offset * 2.5
    shadow_tags = (
        r"{\1c&H000000&\1a&H00&\2c&H000000&\2a&H00&"
        r"\3a&HFF&\4a&HFF&\bord0\shad0\xshad0\yshad0\blur"
        + f"{blur_radius:.4f}"
        + "}"
    )
    position_pattern = re.compile(r"\\pos\((\d+),(\d+)\)")
    position_block_pattern = re.compile(
        r"\{[^{}]*\\(?:pos\(\d+,\d+\)|move\(\d+,\d+,\d+,\d+\))[^{}]*\}"
    )
    result: list[str] = []
    base_events = 0

    for line in lines:
        if not line.startswith("Dialogue:"):
            result.append(line)
            continue

        fields = line.split(",", 9)
        if len(fields) != 10:
            raise ExportValidationError(
                "No se pudo aislar la sombra: evento ASS con estructura inesperada."
            )
        try:
            layer = int(fields[0].split(":", 1)[1].strip())
        except (IndexError, ValueError) as exc:
            raise ExportValidationError(
                "No se pudo aislar la sombra: capa ASS no válida."
            ) from exc

        style = fields[3]
        if (style, layer) not in (("Base", 0), ("Karaoke", 1)):
            raise ExportValidationError(
                "No se pudo aislar la sombra de un tipo de evento ASS no compatible."
            )

        if style == "Base":
            positions = list(position_pattern.finditer(fields[9]))
            position_blocks = list(position_block_pattern.finditer(fields[9]))
            if len(positions) != 1 or len(position_blocks) != 1:
                raise ExportValidationError(
                    "No se pudo conservar la posición de un evento de subtítulo en la sombra."
                )

            shadow_fields = fields.copy()
            shadow_fields[0] = "Dialogue: -1"
            shadow_fields[4] = shadow_marker
            match = positions[0]
            shifted_y = int(match.group(2)) + round(shadow_offset)
            shadow_text = (
                fields[9][:match.start()]
                # Temporal positioning has already split keyframes into static
                # intervals. A zero-distance move keeps this shadow coordinate
                # independent from the foreground's \\pos event.
                + f"\\move({match.group(1)},{shifted_y},{match.group(1)},{shifted_y})"
                + fields[9][match.end():]
            )
            position_block = position_block_pattern.search(shadow_text)
            if position_block is None:
                raise ExportValidationError(
                    "No se pudo aislar la posición ASS del evento de sombra."
                )
            shadow_fields[9] = (
                shadow_text[:position_block.end()]
                + shadow_tags
                + shadow_text[position_block.end():]
            )
            result.append(",".join(shadow_fields))
            base_events += 1

        # The independent blurred mask replaces ASS's hard style shadow. This
        # override changes no foreground color, font, outline, position, or timing.
        fields[9] = r"{\shad0}" + fields[9]
        result.append(",".join(fields))

    if base_events == 0:
        return ass
    return "\n".join(result) + "\n"


def _ass_cues_from_layout(
    record: dict[str, Any], manifest: list[dict[str, Any]], config: DesignerConfig,
    diagnostic_word_ids: dict[tuple[str, float, float, int], str] | None = None,
) -> list[tuple[SubtitleCue, int]]:
    raw_segments = _transcription(record)["segments"]
    formatted_segments = _segments(record, formatted=True)
    lookup: dict[str, TimedWord] = {}
    segment_words: list[list[tuple[str, TimedWord]]] = []
    for segment_index, (raw_segment, (_, _, _, words)) in enumerate(zip(raw_segments, formatted_segments)):
        pairs: list[tuple[str, TimedWord]] = []
        raw_words = raw_segment.get("words", [])
        for word_index, (raw_word, timed_word) in enumerate(zip(raw_words, words)):
            word_id = str(raw_word.get("id") or f"s{segment_index + 1}-w{word_index + 1}")
            if word_id in lookup:
                raise ExportValidationError("Los identificadores de palabra de la transcripción están duplicados.")
            lookup[word_id] = timed_word
            if diagnostic_word_ids is not None:
                diagnostic_word_ids[(
                    timed_word.text, timed_word.start, timed_word.end, timed_word.source_segment,
                )] = word_id
            pairs.append((word_id, timed_word))
        segment_words.append(pairs)

    layouts: dict[str, dict[str, Any]] = {}
    for layout in manifest:
        if not isinstance(layout, dict) or not isinstance(layout.get("active_word_id"), str):
            raise ExportValidationError("El layout ASS contiene una palabra activa no válida.")
        word_id = layout["active_word_id"]
        if word_id in layouts or word_id not in lookup:
            raise ExportValidationError("El layout ASS no corresponde a las palabras guardadas del proyecto.")
        lines = layout.get("lines")
        font_size = layout.get("font_size")
        if (
            not isinstance(lines, list) or not 1 <= len(lines) <= 2
            or any(not isinstance(line, list) or not line or any(not isinstance(item, str) for item in line) for line in lines)
            or not isinstance(font_size, (int, float)) or not 10 <= float(font_size) <= 2000
        ):
            raise ExportValidationError("El layout ASS excede los límites permitidos.")
        render_scale = layout.get("render_scale", 1.0)
        if not isinstance(render_scale, (int, float)) or not 0.1 <= float(render_scale) <= 20:
            raise ExportValidationError("ASS layout render scale is invalid.")
        layouts[word_id] = {"lines": lines, "font_size": float(font_size)}
    if len(layouts) != len(lookup):
        raise ExportValidationError("El layout ASS no incluye todas las palabras temporizadas.")

    cues: list[tuple[SubtitleCue, int]] = []
    for segment_index, pairs in enumerate(segment_words):
        if not pairs:
            start, end, text, _ = formatted_segments[segment_index]
            if text.strip():
                source_word = TimedWord(text, start, end, source_segment=segment_index)
                for cue in segment_transcription(SubtitleDocument(None, None, (source_word,)), config):
                    cues.append((cue, -1))
            continue
        ids = [word_id for word_id, _ in pairs]
        timed = [word for _, word in pairs]
        for active_index, (active_id, active_word) in enumerate(pairs):
            layout = layouts[active_id]
            line_ids = [word_id for line in layout["lines"] for word_id in line]
            if active_id not in line_ids or len(set(line_ids)) != len(line_ids):
                raise ExportValidationError("El layout ASS no contiene la palabra activa o repite palabras.")
            try:
                indices = [ids.index(word_id) for word_id in line_ids]
            except ValueError as exc:
                raise ExportValidationError("Una línea del layout ASS cruza límites de segmentos.") from exc
            if indices != list(range(indices[0], indices[0] + len(indices))):
                raise ExportValidationError("Las palabras del layout ASS deben conservar su orden y continuidad.")
            selected = [timed[index] for index in indices]
            by_id = {word_id: timed[index] for index, word_id in enumerate(ids)}
            lines = tuple(tuple(by_id[word_id] for word_id in line) for line in layout["lines"])
            cue = SubtitleCue(
                active_word.start, active_word.end, tuple(selected), lines=lines,
                font_size=layout["font_size"],
            )
            cues.append((cue, indices.index(active_index)))
    return cues


def _layout_render_scale(manifest: list[dict[str, Any]]) -> float:
    scales = [float(item.get("render_scale", 1.0)) for item in manifest if isinstance(item, dict)]
    if scales and max(scales) - min(scales) > 0.0001:
        raise ExportValidationError("ASS layout mixes player scales.")
    return scales[0] if scales else 1.0


def _ass_with_active_word_windows(
    cues: list[tuple[SubtitleCue, int]], config: DesignerConfig,
    diagnostic_capture: dict[str, Any] | None = None,
    diagnostic_word_ids: dict[tuple[str, float, float, int], str] | None = None,
    validation_resolved_font: ResolvedFont | None = None,
) -> str:
    header = to_ass((), config, validation_font=validation_resolved_font)
    validation_config = _ass_layout_validation_config(config)
    if diagnostic_capture is not None and cues:
        try:
            first_cue = cues[0][0]
            resolved = resolve_font(config.font_name, config.bold, config.fallback_font_names)
            cue_size = first_cue.font_size or config.font_size
            with TextMeasurer(resolved, config.font_size, config.bold) as measurer:
                measured_lines = []
                for words in (first_cue.lines or (first_cue.words,)):
                    text = "".join(word.text for word in words).strip()
                    raw_width = measurer.measure(text, cue_size)
                    decorations = 2 * (config.outline + config.shadow)
                    measured_lines.append({
                        "text": text,
                        "font_size_received_px": cue_size,
                        "font_size_effective_px": max(1, round(cue_size)),
                        "glyph_width_px": raw_width,
                        "outline_shadow_added_px": decorations,
                        "measured_total_width_px": raw_width + decorations,
                    })
            original_limit = config.play_res_x * config.max_width_ratio
            displayed_limit = config.play_res_x * validation_config.max_width_ratio
            diagnostic_capture.update({
                "designer_requested_family": config.font_name,
                "designer_requested_variant": "bold" if config.bold else "regular",
                "resolved_family": resolved.family,
                "resolved_path": str(resolved.path),
                "metric_backend": "Windows GDI" if measurer._image_font is None else "Pillow",
                "cue_font_size_received_px": cue_size,
                "cue_font_size_effective_px": max(1, round(cue_size)),
                "outline_px": config.outline,
                "shadow_px": config.shadow,
                "play_res_x": config.play_res_x,
                "max_width_ratio_before_tolerance": config.max_width_ratio,
                "geometric_limit_px": original_limit,
                "tolerance_px": displayed_limit - original_limit,
                "displayed_limit_px": displayed_limit,
                "effective_acceptance_limit_px": displayed_limit + _DESIGNER_WIDTH_EPSILON_PX,
                "lines": measured_lines,
            })
        except Exception as exc:
            diagnostic_capture["measurement_capture_error"] = str(exc)
    dialogue_lines: list[str] = []
    for cue_index, (cue, active_index) in enumerate(cues, start=1):
        lines = cue.lines or (cue.words,)
        line_pitch = (cue.font_size or config.font_size) * 1.08
        for line_index, words in enumerate(lines):
            line_cue = SubtitleCue(
                cue.start, cue.end, tuple(words), lines=(tuple(words),),
                font_size=cue.font_size,
            )
            # The editor already chose these exact lines using its local-font
            # metrics. Allow only the subpixel discrepancy in Designer's
            # secondary Windows-font validation; max_width_ratio is not
            # serialized into ASS styles, so the rendered design stays intact.
            validation_attempt: dict[str, Any] | None = None
            if diagnostic_capture is not None:
                try:
                    resolved = resolve_font(
                        config.font_name, config.bold, config.fallback_font_names,
                    )
                    cue_size = cue.font_size or config.font_size
                    with TextMeasurer(resolved, config.font_size, config.bold) as measurer:
                        text = "".join(word.text for word in words).strip()
                        glyph_width = measurer.measure(text, cue_size)
                    decoration = 2 * (config.outline + config.shadow)
                    validation_attempt = {
                        "manifest_cue_index": cue_index,
                        "active_word_index_in_cue": active_index,
                        "line_index": line_index + 1,
                        "start": cue.start,
                        "end": cue.end,
                        "text": text,
                        "words": [
                            {
                                "id": (diagnostic_word_ids or {}).get((
                                    word.text, word.start, word.end, word.source_segment,
                                )),
                                "text": word.text,
                                "start": word.start,
                                "end": word.end,
                                "source_segment": word.source_segment,
                            }
                            for word in words
                        ],
                        "requested_family": config.font_name,
                        "resolved_family": resolved.family,
                        "resolved_path": str(resolved.path),
                        "metric_backend": "Windows GDI" if measurer._image_font is None else "Pillow",
                        "font_size_received_px": cue_size,
                        "font_size_effective_px": max(1, round(cue_size)),
                        "glyph_width_px": glyph_width,
                        "outline_px": config.outline,
                        "shadow_px": config.shadow,
                        "decoration_width_px": decoration,
                        "measured_total_width_px": glyph_width + decoration,
                        "geometric_limit_px": config.play_res_x * config.max_width_ratio,
                        "tolerance_px": validation_config.max_width_ratio * config.play_res_x
                        - config.max_width_ratio * config.play_res_x,
                        "effective_limit_px": validation_config.max_width_ratio * config.play_res_x + 0.01,
                    }
                except Exception as exc:
                    validation_attempt = {"measurement_capture_error": str(exc)}
            try:
                part = to_ass(
                    (line_cue,), validation_config,
                    validation_font=validation_resolved_font,
                )
            except Exception as exc:
                if diagnostic_capture is not None and validation_attempt is not None:
                    validation_attempt["validation_error"] = str(exc)
                    diagnostic_capture["failing_validation"] = validation_attempt
                raise
            events = [line for line in part.splitlines() if line.startswith("Dialogue:")]
            y_offset = round((line_index - (len(lines) - 1) / 2) * line_pitch)
            base = next((line for line in events if line.startswith("Dialogue: 0,")), None)
            if base:
                if y_offset:
                    base = re.sub(
                        r"(\\pos\(\d+,)(\d+)(\)\})",
                        lambda match: match.group(1) + str(int(match.group(2)) + y_offset) + match.group(3),
                        base,
                        count=1,
                    )
                dialogue_lines.append(base)
            local_active = active_index
            if active_index >= 0:
                previous = sum(len(line) for line in lines[:line_index])
                local_active = active_index - previous
            if 0 <= local_active < len(words):
                karaoke = [line for line in events if line.startswith("Dialogue: 1,")]
                output_index = sum(1 for word in words[:local_active] if word.text.strip())
                if output_index < len(karaoke):
                    active_line = karaoke[output_index]
                    active_line = re.sub(
                        r"\\alpha&H00&\\kf\d+",
                        lambda _: r"\alpha&H00&\c" + config.active_color + "&",
                        active_line,
                        count=1,
                    )
                    if y_offset:
                        active_line = re.sub(
                            r"(\\pos\(\d+,)(\d+)(\)\})",
                            lambda match: match.group(1) + str(int(match.group(2)) + y_offset) + match.group(3),
                            active_line,
                            count=1,
                        )
                    dialogue_lines.append(active_line)
    return header + "\n".join(dialogue_lines) + ("\n" if dialogue_lines else "")


def export_ass_text(
    record: dict[str, Any], layout_manifest: list[dict[str, Any]] | None = None,
    *, diagnostic_capture: dict[str, Any] | None = None,
    validation_font_path: Path | None = None,
    stage_context: Callable[[str], ContextManager[Any]] | None = None,
) -> str:
    media = record.get("media") if isinstance(record.get("media"), dict) else {}
    width, height = int(media.get("width") or 1920), int(media.get("height") or 1080)
    design = _design(record)
    if diagnostic_capture is not None:
        diagnostic_capture["selected_family"] = str(design.get("fontFamily", "Montserrat ExtraBold"))
        diagnostic_capture["selected_font_size_px"] = design.get("fontSize")
        diagnostic_capture["selected_max_width_percent"] = design.get("maxWidth")
        diagnostic_capture["selected_word_spacing_em"] = design.get("wordSpacing")
        diagnostic_capture["selected_letter_spacing_px"] = design.get("letterSpacing")
        diagnostic_capture["selected_outline_px"] = design.get("outlineWidth")
        diagnostic_capture["selected_shadow"] = design.get("shadow")
        diagnostic_capture["position_x"] = design.get("positionX")
        diagnostic_capture["position_y"] = design.get("positionY")
    render_scale = _layout_render_scale(layout_manifest) if layout_manifest is not None else 1.0
    config = _base_config(record, width, height, render_scale)
    trace_stage = stage_context or (lambda _name: nullcontext())
    if layout_manifest is not None:
        # The manifest was measured by the browser with the selected bundled
        # font. Designer may validate against an installed fallback font, so
        # give its guard the legal format ceiling; the editor's measured layout
        # still controls the actual line breaks and preview width.
        if config.max_width_ratio < .85:
            config = replace(config, max_width_ratio=.85)
        diagnostic_word_ids: dict[tuple[str, float, float, int], str] | None = (
            {} if diagnostic_capture is not None else None
        )
        all_cues = _ass_cues_from_layout(
            record, layout_manifest, config, diagnostic_word_ids,
        )
        with _selected_validation_font(design, validation_font_path) as (
            selected_validation_font, validation_identity,
        ):
            with trace_stage("geometric_validation"):
                _validate_with_selected_font(
                    all_cues, config, design, selected_validation_font,
                    validation_identity, diagnostic_capture,
                )
            with trace_stage("ass_generation"):
                ass = _ass_with_active_word_windows(
                    all_cues, config, diagnostic_capture, diagnostic_word_ids,
                    selected_validation_font,
                )
    else:
        all_cues: list[SubtitleCue] = []
        for index, (start, end, text, words) in enumerate(_segments(record, formatted=True)):
            if not words:
                words = [TimedWord(text, start, end, source_segment=index)]
            document = SubtitleDocument(None, None, tuple(words))
            all_cues.extend(segment_transcription(document, config))
        with _selected_validation_font(design, validation_font_path) as (
            selected_validation_font, validation_identity,
        ):
            with trace_stage("geometric_validation"):
                _validate_with_selected_font(
                    [(cue, -1) for cue in all_cues],
                    config, design, selected_validation_font,
                    validation_identity, diagnostic_capture,
                )
            with trace_stage("ass_generation"):
                ass = to_ass(
                    tuple(all_cues), config,
                    validation_font=selected_validation_font,
                )
    # Designer's measured layout uses its installed-font resolver. Keep that
    # adapter path intact, then point libass at the same licensed local families
    # already bundled for the editor preview.
    measured_family = config.font_name
    selected_family = _ass_font_family(design)
    if measured_family != selected_family:
        ass = ass.replace(f",{measured_family},", f",{selected_family},")
    if str(design.get("fontFamily", "Montserrat ExtraBold")) in {
        "Montserrat ExtraBold", "Montserrat Black", "Anton", "Bebas Neue",
    }:
        # The local TTFs already contain their requested weight. Avoid applying
        # a synthetic bold face on top of those bundled outlines in libass.
        styles: list[str] = []
        for line in ass.splitlines():
            if line.startswith(("Style: Base,", "Style: Karaoke,")):
                fields = line.split(",")
                if len(fields) > 7:
                    fields[7] = "0"
                    line = ",".join(fields)
            styles.append(line)
        ass = "\n".join(styles) + "\n"
    font_scale = ass_font_size_scale(str(design.get("fontFamily", "Montserrat ExtraBold")))
    if font_scale != 1.0:
        scaled_styles: list[str] = []
        for line in ass.splitlines():
            if line.startswith(("Style: Base,", "Style: Karaoke,")):
                fields = line.split(",")
                if len(fields) > 2:
                    fields[2] = f"{float(fields[2]) * font_scale:g}"
                    line = ",".join(fields)
            scaled_styles.append(line)
        ass = "\n".join(scaled_styles) + "\n"
        ass = re.sub(
            r"\\fs(\d+(?:\.\d+)?)",
            lambda match: r"\fs" + f"{float(match.group(1)) * font_scale:g}",
            ass,
        )
    letter_scale = render_scale if layout_manifest is not None else width / 1080
    letter_spacing = float(design.get("letterSpacing", 0)) * letter_scale
    if letter_spacing:
        ass = ass.replace(",100,100,0,0,1,", f",100,100,{letter_spacing:g},0,1,")
    ass = _apply_temporal_positions(ass, design, config)
    ass = _align_ass_rendering_to_preview(
        ass,
        design,
        str(design.get("fontFamily", "Montserrat ExtraBold")),
        font_scale,
    )
    if design.get("shadow", True):
        ass = _add_independent_shadow_layer(ass, config.shadow)
    return ass

