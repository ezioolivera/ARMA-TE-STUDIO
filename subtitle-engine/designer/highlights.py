"""Configurable lexical emphasis, kept separate from active-word karaoke."""

import re
import unicodedata
from dataclasses import replace

from .config import DesignerConfig
from .models import SubtitleDocument


def _key(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"^\W+|\W+$", "", normalized)


def mark_highlights(
    document: SubtitleDocument, config: DesignerConfig
) -> SubtitleDocument:
    """Mark words matching the configurable keyword list (accent-insensitive)."""
    keys = {_key(word) for word in config.highlight_words}
    return replace(
        document,
        words=tuple(replace(word, highlighted=_key(word.text) in keys) for word in document.words),
    )

