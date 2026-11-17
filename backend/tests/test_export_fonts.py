from pathlib import Path
import re
import shutil
import tempfile
import unittest

from fontTools.ttLib import TTFont

from app.api.v1.exports import _verify_ass_font
from app.exports.fonts import (
    ass_font_layout_metrics, ass_font_size_scale, materialize_bundled_font,
    validation_font,
)


class BundledFontTests(unittest.TestCase):
    @unittest.skipUnless(__import__("os").name == "nt", "Requires Windows GDI private font registration")
    def test_geometric_validation_uses_each_exact_private_ttf_and_preserves_limits(self):
        from designer import DesignerConfig, ResolvedFont, SubtitleCue, TimedWord, validate_cues
        from designer.metrics import TextMeasurer

        profiles = (
            ("Anton", 400),
            ("Bebas Neue", 400),
            ("Montserrat ExtraBold", 800),
            ("Montserrat Black", 900),
        )
        for family, weight in profiles:
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                path = materialize_bundled_font(family, Path(directory))
                with validation_font(path, family, weight) as (face_path, alias, identity):
                    self.assertEqual(identity.weight_class, weight)
                    self.assertEqual(identity.resolved_family, alias)
                    self.assertNotEqual(alias.casefold(), "arial narrow")
                    self.assertEqual(identity.metric_backend, "Windows GDI (FR_PRIVATE, cara verificada)")
                    self.assertNotEqual(identity.source_sha256, identity.registered_sha256)
                    resolved = ResolvedFont(alias, face_path, family)
                    with TextMeasurer(resolved, 96, True) as measurer:
                        spanish = "CORAZ\u00d3N EXPERIMENTO INCRE\u00cdBLE NI\u00d1O"
                        measured = measurer.measure(spanish, 96)
                        self.assertGreater(measured, 0)
                        self.assertEqual(measurer.font.path, face_path.resolve())

                    config = DesignerConfig.for_video(
                        1920, 1080, font_name=alias, fallback_font_names=(),
                        font_size=96, min_font_size=10, bold=True,
                        outline=8, shadow=2, max_width_ratio=.85,
                        watermark_box=None, safe_top_ratio=0, safe_bottom_ratio=0,
                    )
                    self.assertEqual(config.max_width_ratio, .85)
                    within = SubtitleCue(
                        0, 1, (TimedWord(" CORAZ\u00d3N NI\u00d1O", 0, 1),),
                    )
                    validate_cues((within,), config, resolved)
                    overflowing = SubtitleCue(
                        0, 1, (TimedWord(" " + "EXPERIMENTO " * 40, 0, 1),),
                    )
                    with self.assertRaisesRegex(ValueError, "line 1"):
                        validate_cues((overflowing,), config, resolved)

    def test_ass_font_size_scale_uses_os2_windows_metrics(self):
        expected_weights = {
            "Anton": 400,
            "Bebas Neue": 400,
            "Montserrat ExtraBold": 800,
            "Montserrat Black": 900,
        }
        for family in expected_weights:
            with self.subTest(family=family):
                with tempfile.TemporaryDirectory() as directory:
                    path = materialize_bundled_font(family, Path(directory))
                    with TTFont(path) as font:
                        expected = (
                            font["OS/2"].usWinAscent + font["OS/2"].usWinDescent
                        ) / font["head"].unitsPerEm
                        hhea = (
                            font["hhea"].ascent - font["hhea"].descent
                        ) / font["head"].unitsPerEm
                    self.assertAlmostEqual(ass_font_size_scale(family), expected, places=8)
                    self.assertNotAlmostEqual(ass_font_size_scale(family), hhea, places=3)

    def test_ass_layout_metrics_are_derived_from_space_and_css_line_metrics(self):
        for family in ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Montserrat Black"):
            with self.subTest(family=family):
                with tempfile.TemporaryDirectory() as directory:
                    path = materialize_bundled_font(family, Path(directory))
                    with TTFont(path) as font:
                        upm = font["head"].unitsPerEm
                        os2 = font["OS/2"]
                        if os2.fsSelection & (1 << 7):
                            css_ascent, css_descent = os2.sTypoAscender, os2.sTypoDescender
                        else:
                            css_ascent, css_descent = font["hhea"].ascent, font["hhea"].descent
                        space = font.getBestCmap()[ord(" ")]
                        expected_space = font["hmtx"][space][0] / upm
                        expected_baseline = (
                            css_ascent + css_descent - os2.usWinAscent + os2.usWinDescent
                        ) / (2 * upm)
                actual_space, actual_baseline = ass_font_layout_metrics(family)
                self.assertAlmostEqual(actual_space, expected_space, places=8)
                self.assertAlmostEqual(actual_baseline, expected_baseline, places=8)

    def test_local_families_convert_to_static_ttf_with_spanish_glyphs(self):
        for family, expected_weight in (
            ("Anton", 400),
            ("Bebas Neue", 400),
            ("Montserrat ExtraBold", 800),
            ("Montserrat Black", 900),
        ):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                path = materialize_bundled_font(family, Path(directory))
                self.assertIsNotNone(path)
                self.assertEqual(path.suffix, ".ttf")
                with TTFont(path) as font:
                    self.assertIsNone(font.flavor)
                    self.assertEqual(font["OS/2"].usWeightClass, expected_weight)
                    cmap = set().union(*(set(table.cmap) for table in font["cmap"].tables))
                    self.assertTrue(all(ord(char) in cmap for char in "áéíóúüñÁÉÍÓÚÜÑ¿¡"))
                    family_names = {
                        record.toUnicode()
                        for record in font["name"].names
                        if record.nameID in (1, 16)
                    }
                    self.assertIn(family, family_names)

    def test_woff2_sources_are_not_modified(self):
        from app.exports.fonts import font_assets_directory

        sources = list(font_assets_directory().glob("*.woff2"))
        before = {path.name: path.read_bytes() for path in sources}
        with tempfile.TemporaryDirectory() as directory:
            materialize_bundled_font("Anton", Path(directory))
            materialize_bundled_font("Montserrat Black", Path(directory))
        self.assertEqual(before, {path.name: path.read_bytes() for path in sources})

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for a live libass font check")
    def test_ffmpeg_libass_selects_each_bundled_family_instead_of_a_fallback(self):
        from app.exports.fonts import font_assets_directory

        ffmpeg = shutil.which("ffmpeg")
        for family in ("Anton", "Bebas Neue", "Montserrat ExtraBold", "Montserrat Black"):
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                font_dir = Path(directory) / "fonts"
                font_dir.mkdir()
                materialize_bundled_font(family, font_dir)
                ass_path = Path(directory) / "font-check.ass"
                ass_path.write_text(
                    """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Base,""" + family + """,96,&H00FFFFFF,&H00D5D737,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,8,0.8,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.10,Base,,0,0,0,,ÁRBOL, NIÑO, ACCIÓN, ÚLTIMA.
""",
                    encoding="utf-8",
                )
                # Reuse the same family-probe path used by the MP4 worker.
                selected = __import__("asyncio").run(
                    _verify_ass_font(ffmpeg, ass_path, font_dir, family)
                )
                selected_key = re.sub(r"[^a-z0-9]", "", selected.casefold())
                family_key = re.sub(r"[^a-z0-9]", "", family.casefold())
                self.assertIn(selected_key, {family_key, family_key + "regular"})


if __name__ == "__main__":
    unittest.main()
