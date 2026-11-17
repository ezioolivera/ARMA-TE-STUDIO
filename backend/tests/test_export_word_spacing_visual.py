"""Pixel-level browser/libass checks for the export word-spacing adapter."""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.exports.adapters import _align_ass_rendering_to_preview
from app.exports.fonts import (
    ass_font_layout_metrics,
    ass_font_size_scale,
    font_assets_directory,
    materialize_bundled_font,
)


def _chrome_executable() -> str | None:
    candidates = (
        shutil.which("chrome"),
        shutil.which("chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe"),
        str(Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe"),
    )
    return next((path for path in candidates if path and Path(path).is_file()), None)


def _style_header(family: str, size: float) -> str:
    format_line = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    styles = [
        f"Style: Base,{family},{size:g},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
        f"Style: Karaoke,{family},{size:g},&H00D7D537,&H00FFFFFF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1",
    ]
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 2200\n"
        "WrapStyle: 2\nScaledBorderAndShadow: yes\n[V4+ Styles]\n"
        f"{format_line}\n" + "\n".join(styles) +
        "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def _dialogue(layer: int, start: str, end: str, style: str, y: int, text: str) -> str:
    return (
        f"Dialogue: {layer},{start},{end},{style},,0,0,0,,"
        f"{{\\an5\\pos(960,{y})}}{text}"
    )


def _visible_bounds(
    pixels: bytes, width: int, center_y: int, half_height: int, channels: int = 1,
) -> tuple[int, int]:
    xs: list[int] = []
    top = max(0, center_y - half_height)
    bottom = min(2200, center_y + half_height)
    for x in range(width):
        for y in range(top, bottom):
            offset = (y * width + x) * channels
            sample = pixels[offset:offset + channels]
            if (max(sample) if channels > 1 else sample[0]) > 100:
                xs.append(x)
                break
    if not xs:
        raise AssertionError(f"No rendered subtitle pixels near y={center_y}")
    return min(xs), max(xs)


def _measure_width(pixels: bytes, y: int, size: int, *, channels: int = 1) -> tuple[int, tuple[int, int]]:
    bounds = _visible_bounds(pixels, 1920, y, max(30, size), channels)
    return bounds[1] - bounds[0] + 1, bounds


@unittest.skipUnless(shutil.which("ffmpeg") and _chrome_executable(), "Requires FFmpeg and local Chrome")
class WordSpacingRasterTests(unittest.TestCase):
    profiles = (
        ("Anton", "anton-latin.woff2", 400),
        ("Bebas Neue", "bebas-neue-latin.woff2", 400),
        ("Montserrat ExtraBold", "montserrat-latin-variable.woff2", 800),
        ("Montserrat Black", "montserrat-latin-variable.woff2", 900),
    )
    configurations = ((42, .08), (66, .16), (96, .28))
    phrases = {
        2: "MITAD A LA",
        3: "MITAD A LA OTRA",
        5: "MITAD A LA OTRA TENÍAS QUE",
    }

    def test_browser_and_libass_word_gap_contribution_with_local_faces(self):
        chrome = _chrome_executable()
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(chrome)
        self.assertIsNotNone(ffmpeg)
        assets = font_assets_directory()

        for family, source_name, weight in self.profiles:
            for font_size, word_spacing in self.configurations:
                with self.subTest(family=family, font_size=font_size, word_spacing=word_spacing):
                    with tempfile.TemporaryDirectory(prefix="arma-word-gap-raster-") as temporary:
                        root = Path(temporary)
                        fonts = root / "fonts"
                        fonts.mkdir()
                        materialize_bundled_font(family, fonts)

                        font_data = base64.b64encode((assets / source_name).read_bytes()).decode("ascii")
                        css_family = '"Montserrat Variable"' if family.startswith("Montserrat") else f'"{family}"'
                        css_weight = "100 900" if family.startswith("Montserrat") else str(weight)
                        css_rows: list[str] = []
                        ass_rows: list[str] = []
                        row_y: dict[tuple[str, int], int] = {}
                        y = 120
                        for separators, phrase in self.phrases.items():
                            words = phrase.split()
                            for kind in ("gap", "nogap"):
                                row_y[(kind, separators)] = y
                                css_gap = font_size * word_spacing if kind == "gap" else 0
                                css_rows.append(
                                    f'<div class="line" style="top:{y-font_size//2}px;gap:{css_gap}px">'
                                    + "".join(f"<span>{word}</span>" for word in words)
                                    + "</div>"
                                )
                                if kind == "gap":
                                    ass_text = " ".join(words)
                                else:
                                    # Separate shaping runs just as the preview's word spans do,
                                    # without introducing a visible separator in the baseline.
                                    ass_text = r"{\fsp0}".join(words)
                                ass_rows.append(_dialogue(0, "0:00:00.00", "0:00:02.00", "Base", y, ass_text))
                                y += 145

                        # Also exercise an explicit two-line cue and a karaoke overlay with
                        # an active turquoise word; both use the same separator adapter.
                        two_line_y = y + 35
                        two_line = "MITAD A LA\\NOTRA TENÍAS QUE"
                        css_rows.append(
                            f'<div class="block" style="top:{two_line_y-font_size}px">'
                            f'<div class="line" style="gap:{font_size*word_spacing}px">'
                            + "".join(f"<span>{word}</span>" for word in "MITAD A LA".split())
                            + f'</div><div class="line" style="gap:{font_size*word_spacing}px">'
                            + "".join(f"<span>{word}</span>" for word in "OTRA TENÍAS QUE".split())
                            + "</div></div>"
                        )
                        ass_rows.append(_dialogue(0, "0:00:00.00", "0:00:02.00", "Base", two_line_y, two_line))

                        karaoke_y = two_line_y + 145
                        karaoke_words = "MITAD A LA OTRA".split()
                        karaoke_base = " ".join(karaoke_words)
                        karaoke_overlay = "".join(
                            (r"{\alpha&H00&\kf100}" if index == 2 else r"{\alpha&HFF&}")
                            + (" " if index else "") + word
                            for index, word in enumerate(karaoke_words)
                        )
                        css_rows.append(
                            f'<div class="line" style="top:{karaoke_y-font_size//2}px;gap:{font_size*word_spacing}px">'
                            + "".join(
                                f'<span style="color:{"#37D5D7" if index == 2 else "white"}">{word}</span>'
                                for index, word in enumerate(karaoke_words)
                            )
                            + "</div>"
                        )
                        ass_rows.extend((
                            _dialogue(0, "0:00:00.00", "0:00:02.00", "Base", karaoke_y, karaoke_base),
                            _dialogue(1, "0:00:00.00", "0:00:02.00", "Karaoke", karaoke_y, karaoke_overlay),
                        ))

                        html = root / "preview.html"
                        screenshot = root / "preview.png"
                        html.write_text(
                            "<!doctype html><meta charset='utf-8'><style>"
                            f"@font-face{{font-family:{css_family};src:url(data:font/woff2;base64,{font_data}) format('woff2');font-weight:{css_weight}}}"
                            "*{box-sizing:border-box}html,body{margin:0;width:1920px;height:2200px;background:#000}"
                            f".line{{position:absolute;left:0;width:1920px;display:flex;justify-content:center;white-space:nowrap;"
                            f"font-family:{css_family};font-weight:{weight};font-size:{font_size}px;color:#fff;line-height:1.08}}"
                            f".block{{position:absolute;left:0;width:1920px;display:flex;flex-direction:column;align-items:center;"
                            f"font-family:{css_family};font-weight:{weight};font-size:{font_size}px;color:#fff;line-height:1.08}}"
                            ".block .line{position:static;width:auto}" 
                            "</style>" + "".join(css_rows)
                            + "<script>document.fonts.ready.then(()=>document.body.dataset.fontsReady='yes')</script>",
                            encoding="utf-8",
                        )
                        chrome_result = subprocess.run(
                            [chrome, "--headless", "--disable-gpu", "--no-sandbox",
                             f"--user-data-dir={root / 'chrome-profile'}", "--virtual-time-budget=3000",
                             f"--screenshot={screenshot}", "--window-size=1920,2200", html.as_uri()],
                            cwd=root, capture_output=True, check=True, timeout=30,
                        )
                        self.assertTrue(screenshot.is_file(), chrome_result.stderr.decode("utf-8", "replace"))
                        preview = subprocess.run(
                            [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(screenshot),
                             "-vf", "format=gray", "-f", "rawvideo", "-"],
                            capture_output=True, check=True,
                        ).stdout

                        ass_path = root / "captions.ass"
                        ass_path.write_text(
                            _style_header(family, font_size * ass_font_size_scale(family))
                            + "\n".join(ass_rows) + "\n",
                            encoding="utf-8",
                        )
                        design = {"wordSpacing": word_spacing}
                        ass_path.write_text(
                            _align_ass_rendering_to_preview(
                                ass_path.read_text(encoding="utf-8"), design, family,
                                ass_font_size_scale(family),
                            ),
                            encoding="utf-8",
                        )
                        rendered = subprocess.run(
                            [ffmpeg, "-hide_banner", "-loglevel", "verbose", "-f", "lavfi", "-i",
                             "color=c=black:s=1920x2200:d=1:r=1", "-vf",
                             f"setpts=PTS+0.5/TB,subtitles=filename={ass_path.name}:fontsdir=fonts", "-frames:v", "1",
                             "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                            cwd=root, capture_output=True, check=True,
                        )
                        fontselect = [line for line in rendered.stderr.decode("utf-8", "replace").splitlines()
                                      if "fontselect:" in line.lower()]
                        self.assertTrue(fontselect)
                        resolved = fontselect[0].casefold()
                        for expected_name in (family.casefold().replace(" ", ""),):
                            self.assertIn(expected_name, re.sub(r"[^a-z0-9]", "", resolved))

                        ass_pixels = rendered.stdout
                        for separators in self.phrases:
                            y_gap = row_y[("gap", separators)]
                            y_nogap = row_y[("nogap", separators)]
                            preview_gap_width, preview_gap_bounds = _measure_width(preview, y_gap, font_size)
                            preview_nogap_width, _ = _measure_width(preview, y_nogap, font_size)
                            ass_gap_width, ass_gap_bounds = _measure_width(ass_pixels, y_gap, font_size, channels=3)
                            ass_nogap_width, _ = _measure_width(ass_pixels, y_nogap, font_size, channels=3)
                            preview_separator_advance = preview_gap_width - preview_nogap_width
                            ass_separator_advance = ass_gap_width - ass_nogap_width
                            self.assertLessEqual(
                                abs(ass_separator_advance - preview_separator_advance), 2,
                                f"{family} {font_size}px, {separators} separators: "
                                f"Preview advance={preview_separator_advance}px, libass={ass_separator_advance}px",
                            )
                            self.assertLessEqual(
                                abs(sum(preview_gap_bounds) - sum(ass_gap_bounds)) / 2, 2,
                                f"{family} {font_size}px centering differs by more than 2px",
                            )

                        karaoke_bounds = _visible_bounds(ass_pixels, 1920, karaoke_y, font_size, channels=3)
                        preview_karaoke_bounds = _visible_bounds(preview, 1920, karaoke_y, font_size)
                        self.assertLessEqual(abs(sum(karaoke_bounds) / 2 - 960), 2)
                        self.assertLessEqual(abs(sum(preview_karaoke_bounds) / 2 - sum(karaoke_bounds) / 2), 2)
                        two_line_preview_bounds = _visible_bounds(preview, 1920, two_line_y, 2 * font_size)
                        two_line_ass_bounds = _visible_bounds(ass_pixels, 1920, two_line_y, 2 * font_size, channels=3)
                        self.assertLessEqual(abs(sum(two_line_preview_bounds) / 2 - sum(two_line_ass_bounds) / 2), 2)
                        self.assertIn(r"\kf100", ass_path.read_text(encoding="utf-8"))
                        serialized = ass_path.read_text(encoding="utf-8")
                        self.assertIn("Style: Karaoke", serialized)
                        self.assertIn("&H00D7D537", serialized)
                        self.assertIn(r"\alpha&H00&", serialized)
                        self.assertIn(r"\N", serialized)


if __name__ == "__main__":
    unittest.main()
