"""Materialize the project's licensed WOFF2 fonts as temporary libass TTFs."""

from __future__ import annotations

from functools import lru_cache
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import hashlib
import logging
import os
from pathlib import Path
import re
import tempfile
from typing import Iterator
from uuid import uuid4

logger = logging.getLogger(__name__)


class FontPreparationError(ValueError):
    """A bundled font could not be converted or lacks required glyphs."""


@dataclass(frozen=True, slots=True)
class ValidationFont:
    """Exact local font face temporarily exposed to a metric backend."""

    resolved_family: str
    resolved_path: Path
    source_path: Path
    source_sha256: str
    registered_sha256: str
    weight_class: int
    metric_backend: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _font_weight(path: Path) -> int:
    try:
        from fontTools.ttLib import TTFont

        with TTFont(path, lazy=True) as font:
            return int(font["OS/2"].usWeightClass)
    except Exception as exc:
        raise FontPreparationError(f"No se pudo comprobar el peso de {path.name}: {exc}") from exc


def _alias_ttf(source: Path, destination: Path, alias: str) -> None:
    """Change only family-name metadata so GDI cannot choose an installed lookalike."""
    try:
        from fontTools.ttLib import TTFont

        with TTFont(source) as font:
            _set_name_table(font, alias)
            font.save(destination)
        with TTFont(source, lazy=True) as original, TTFont(destination, lazy=True) as copied:
            preserved = set(original.keys()) - {"name", "head", "GlyphOrder"}
            if preserved != set(copied.keys()) - {"name", "head", "GlyphOrder"}:
                raise FontPreparationError("El alias temporal cambió la estructura del TTF.")
            for table in preserved:
                if original.getTableData(table) != copied.getTableData(table):
                    raise FontPreparationError(
                        f"El alias temporal alteró la tabla tipográfica {table}."
                    )
    except FontPreparationError:
        raise
    except Exception as exc:
        raise FontPreparationError(f"No se pudo preparar el alias GDI temporal: {exc}") from exc


@contextmanager
def validation_font(
    font_path: Path,
    requested_family: str,
    expected_weight: int,
) -> Iterator[tuple[Path, str, ValidationFont]]:
    """Expose an exact TTF to GDI privately, or use Pillow's explicit path.

    On Windows the unique family alias prevents system-font substitution. The
    selected face is verified through GetTextFaceW after a real measurement.
    Registration is process-private and always removed before its temp files.
    """
    path = Path(font_path).resolve(strict=True)
    actual_weight = _font_weight(path)
    if actual_weight != expected_weight:
        raise FontPreparationError(
            f"La fuente {requested_family} tiene peso OS/2 {actual_weight}; "
            f"se esperaba {expected_weight}."
        )
    source_hash = _sha256(path)

    try:
        from PIL import ImageFont  # noqa: F401
    except ImportError:
        ImageFont = None

    if os.name != "nt":
        if ImageFont is None:
            raise FontPreparationError(
                "No se puede medir la fuente exacta: fuera de Windows se requiere Pillow."
            )
        identity = ValidationFont(
            requested_family, path, path, source_hash, source_hash,
            actual_weight, "Pillow (ruta TTF explícita)",
        )
        logger.info(
            "Exact export font metrics: requested=%s weight=%d resolved=%s path=%s sha256=%s backend=%s",
            requested_family, actual_weight, identity.resolved_family, identity.resolved_path,
            identity.source_sha256, identity.metric_backend,
        )
        yield path, requested_family, identity
        return

    with tempfile.TemporaryDirectory(prefix="arma-te-validation-font-") as temporary:
        alias = f"ATV{os.getpid():X}{uuid4().hex[:12].upper()}"
        alias_path = Path(temporary) / "validation-face.ttf"
        _alias_ttf(path, alias_path, alias)
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        add_font = gdi.AddFontResourceExW
        add_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
        add_font.restype = ctypes.c_int
        remove_font = gdi.RemoveFontResourceExW
        remove_font.argtypes = (ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
        remove_font.restype = ctypes.c_int
        private = 0x10  # FR_PRIVATE; visible only to this process.
        if add_font(str(alias_path), private, None) <= 0:
            raise FontPreparationError(
                f"Windows no pudo registrar privadamente el TTF exacto de {requested_family}."
            )
        try:
            from designer.fonts import ResolvedFont
            from designer.metrics import TextMeasurer

            resolved = ResolvedFont(alias, alias_path, requested_family)
            with TextMeasurer(resolved, 32, False) as measurer:
                if measurer._image_font is not None:
                    raise FontPreparationError(
                        "Se esperaba Windows GDI, pero la medición seleccionó Pillow."
                    )
                measurer.measure("CORAZ\u00d3N NI\u00d1O", 32)
                get_face = gdi.GetTextFaceW
                get_face.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
                get_face.restype = ctypes.c_int
                selected = ctypes.create_unicode_buffer(128)
                if not get_face(measurer._dc, len(selected), selected):
                    raise FontPreparationError(
                        "Windows no pudo identificar la fuente seleccionada por GDI."
                    )
                if selected.value.casefold() != alias.casefold():
                    raise FontPreparationError(
                        f"GDI sustituyó {requested_family}: se solicitó el TTF privado "
                        f"{alias}, pero se seleccionó {selected.value or 'una fuente desconocida'}."
                    )
            identity = ValidationFont(
                alias, alias_path, path, source_hash, _sha256(alias_path),
                actual_weight, "Windows GDI (FR_PRIVATE, cara verificada)",
            )
            logger.info(
                "Exact export font metrics: requested=%s weight=%d resolved=%s path=%s sha256=%s backend=%s",
                requested_family, actual_weight, identity.resolved_family, identity.resolved_path,
                identity.source_sha256, identity.metric_backend,
            )
            yield alias_path, alias, identity
        finally:
            if remove_font(str(alias_path), private, None) <= 0:
                raise FontPreparationError(
                    f"Windows no pudo retirar el registro privado temporal de {requested_family}."
                )


def font_assets_directory() -> Path:
    return Path(__file__).resolve().parents[3] / "frontend" / "src" / "assets" / "fonts"


@lru_cache(maxsize=4)
def ass_font_size_scale(font_family: str) -> float:
    """Convert CSS em sizing to ASS font size for the bundled local face.

    CSS font-size scales outlines from unitsPerEm. libass interprets ASS ``\\fs``
    using the Windows OS/2 ``usWinAscent + usWinDescent`` span (to match GDI),
    so the conversion is that span divided by unitsPerEm. The hhea span is not
    interchangeable: it made the exported faces too small compared with CSS.
    """
    sources = _FONT_FILES.get(font_family)
    if sources is None:
        return 1.0
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(font_assets_directory() / sources[0], lazy=True)
        units_per_em = font["head"].unitsPerEm
        os2 = font["OS/2"]
        win_span = os2.usWinAscent + os2.usWinDescent
        if units_per_em <= 0 or win_span <= 0:
            raise FontPreparationError(
                f"Las métricas OS/2 de {font_family} no sirven para calcular el tamaño ASS."
            )
        scale = win_span / units_per_em
        font.close()
    except Exception as exc:
        raise FontPreparationError(
            f"No se pudieron leer las métricas de la fuente {font_family}: {exc}"
        ) from exc
    if not 0.5 < scale < 2.5:
        raise FontPreparationError(f"Las métricas de {font_family} no son válidas para libass.")
    return scale


@lru_cache(maxsize=4)
def ass_font_layout_metrics(font_family: str) -> tuple[float, float] | None:
    """Return (space advance / UPM, ASS-to-CSS baseline shift / em).

    Browser line boxes use the font's typographic ascender/descender when the
    font opts into USE_TYPO_METRICS, otherwise its hhea metrics. libass centers
    ASS rows from the OS/2 Windows ascent/descent span. The second value is the
    position correction needed to put the libass baseline at the CSS baseline;
    it is a ratio, so callers multiply it by that cue's CSS font size.
    """
    sources = _FONT_FILES.get(font_family)
    if sources is None:
        return None
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(font_assets_directory() / sources[0], lazy=True)
        if font_family.startswith("Montserrat "):
            from fontTools.varLib.instancer import instantiateVariableFont

            weight = 800 if font_family == "Montserrat ExtraBold" else 900
            instantiateVariableFont(font, {"wght": weight}, inplace=True, updateFontNames=True)
        units_per_em = font["head"].unitsPerEm
        os2 = font["OS/2"]
        win_ascent = os2.usWinAscent
        win_descent = os2.usWinDescent
        if os2.fsSelection & (1 << 7):
            css_ascent = os2.sTypoAscender
            css_descent = os2.sTypoDescender
        else:
            css_ascent = font["hhea"].ascent
            css_descent = font["hhea"].descent
        space_glyph = font.getBestCmap().get(ord(" "))
        if not units_per_em or not space_glyph:
            raise FontPreparationError(f"Faltan métricas tipográficas en {font_family}.")
        space_advance = font["hmtx"][space_glyph][0] / units_per_em
        baseline_shift_per_em = (
            (css_ascent + css_descent) - (win_ascent - win_descent)
        ) / (2 * units_per_em)
        font.close()
    except Exception as exc:
        raise FontPreparationError(
            f"No se pudieron leer las métricas de composición de {font_family}: {exc}"
        ) from exc
    if not 0 <= space_advance <= 1 or abs(baseline_shift_per_em) > 0.5:
        raise FontPreparationError(f"Las métricas de composición de {font_family} no son válidas.")
    return space_advance, baseline_shift_per_em


_FONT_FILES: dict[str, tuple[str, ...]] = {
    "Montserrat ExtraBold": (
        "montserrat-latin-variable.woff2",
        "montserrat-latin-ext-variable.woff2",
    ),
    "Montserrat Black": (
        "montserrat-latin-variable.woff2",
        "montserrat-latin-ext-variable.woff2",
    ),
    "Anton": ("anton-latin.woff2", "anton-latin-ext.woff2"),
    "Bebas Neue": ("bebas-neue-latin.woff2", "bebas-neue-latin-ext.woff2"),
}

_SPANISH_GLYPHS = "áéíóúüñÁÉÍÓÚÜÑ¿¡"


def _set_name_table(font, family: str, style: str = "Regular") -> None:
    """Assign stable Windows/macOS family names for libass and avoid aliases."""
    postscript = re.sub(r"[^A-Za-z0-9-]", "", family.replace(" ", "-"))
    values = {
        1: family,
        2: style,
        4: f"{family} {style}" if style != "Regular" else family,
        6: f"{postscript}-{style}" if style != "Regular" else postscript,
        16: family,
        17: style,
    }
    names = font["name"]
    records = list(names.names)
    for record in records:
        value = values.get(record.nameID)
        if value is not None:
            names.setName(value, record.nameID, record.platformID, record.platEncID, record.langID)
    # WOFF subsets normally include Windows records. Add them when a distributor
    # strips a typographic-family record, so family matching stays deterministic.
    for name_id, value in values.items():
        if not any(record.nameID == name_id and record.platformID == 3 for record in records):
            names.setName(value, name_id, 3, 1, 0x409)


def materialize_bundled_font(font_family: str, destination: Path) -> Path | None:
    """Convert a selected local WOFF2 family to a static TTF in ``destination``.

    Latin and Latin Extended subsets are merged so Spanish accents, Ñ and other
    editor text do not rely on a system fallback. The source assets are read-only;
    the generated TTF is intended for a short-lived FFmpeg ``fontsdir``.
    """
    sources = _FONT_FILES.get(font_family)
    if sources is None:
        return None

    try:
        from fontTools.merge import Merger
        from fontTools.ttLib import TTFont
        from fontTools.varLib.instancer import instantiateVariableFont
    except ImportError as exc:  # pragma: no cover - exercised in missing-dependency installs
        raise FontPreparationError(
            "La exportación con fuentes locales requiere FontTools con soporte WOFF2. "
            "Instala las dependencias de backend actualizadas."
        ) from exc

    source_dir = font_assets_directory()
    source_paths = [source_dir / name for name in sources]
    missing_files = [path.name for path in source_paths if not path.is_file()]
    if missing_files:
        raise FontPreparationError(
            f"Faltan fuentes licenciadas del proyecto: {', '.join(missing_files)}."
        )

    destination.mkdir(parents=True, exist_ok=True)
    try:
        if font_family.startswith("Montserrat "):
            weight = 800 if font_family == "Montserrat ExtraBold" else 900
            static_subsets = []
            for index, source in enumerate(source_paths):
                font = TTFont(source)
                instantiateVariableFont(
                    font, {"wght": weight}, inplace=True, updateFontNames=True,
                )
                font.flavor = None
                subset_path = destination / f"montserrat-{weight}-{index}.ttf"
                font.save(subset_path)
                static_subsets.append(str(subset_path))
            font = Merger().merge(static_subsets)
            _set_name_table(font, font_family)
            output = destination / f"{weight}-{font_family.replace(' ', '-')}.ttf"
        else:
            font = Merger().merge([str(path) for path in source_paths])
            output = destination / f"{font_family.lower().replace(' ', '-')}.ttf"

        font.flavor = None
        font.save(output)
        # The intermediate instances are no longer needed after subset merging.
        for path in destination.glob("montserrat-*-*.ttf"):
            if path != output:
                path.unlink(missing_ok=True)

        check = TTFont(output, lazy=True)
        codepoints = set().union(*(set(table.cmap) for table in check["cmap"].tables))
        missing_glyphs = [char for char in _SPANISH_GLYPHS if ord(char) not in codepoints]
        if missing_glyphs:
            raise FontPreparationError(
                f"La fuente {font_family} no incluye caracteres españoles requeridos: "
                f"{''.join(missing_glyphs)}."
            )
        if check.flavor is not None or output.suffix.lower() != ".ttf":
            raise FontPreparationError(f"La fuente {font_family} no se convirtió a TTF estático.")
        check.close()
    except FontPreparationError:
        raise
    except Exception as exc:
        raise FontPreparationError(
            f"No se pudo preparar la fuente local {font_family}: {exc}"
        ) from exc
    return output
