"""Subtitle grouping, styling, and ASS/SRT export."""

from .config import DesignerConfig
from .exporters import export_ass, export_srt, to_ass, to_srt
from .fonts import ResolvedFont, resolve_font
from .highlights import mark_highlights
from .layout import validate_cues
from .loader import load_transcription
from .models import SubtitleCue, SubtitleDocument, TimedWord
from .segmenter import segment_transcription

__all__ = [
    "DesignerConfig",
    "ResolvedFont",
    "SubtitleCue",
    "SubtitleDocument",
    "TimedWord",
    "export_ass",
    "export_srt",
    "load_transcription",
    "mark_highlights",
    "resolve_font",
    "segment_transcription",
    "to_ass",
    "to_srt",
    "validate_cues",
]
