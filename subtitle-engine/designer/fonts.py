"""Resolve configured font families to installed font files with fallback."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResolvedFont:
    family: str
    path: Path
    requested_family: str

    @property
    def used_fallback(self) -> bool:
        return self.family.casefold() != self.requested_family.casefold()


def _family_key(value: str) -> str:
    value = re.sub(r"\s*\((?:TrueType|OpenType)\)$", "", value, flags=re.I)
    value = re.sub(
        r"\b(bold|negrita|fett|gras|italic|cursiva|kursiv|oblique|regular|"
        r"light|medium|semibold)\b", "", value, flags=re.I,
    )
    return " ".join(value.casefold().split())


def _is_bold(face_label: str) -> bool:
    return bool(re.search(r"\b(bold|negrita|fett|gras)\b", face_label, re.I))


def _windows_font_entries() -> list[tuple[str, bool, Path]]:
    try:
        import winreg
    except ImportError:
        return []

    entries: list[tuple[str, bool, Path]] = []
    font_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    face_label, filename, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if not isinstance(filename, str):
                    continue
                path = Path(os.path.expandvars(filename))
                if not path.is_absolute():
                    path = font_dir / path
                if path.is_file():
                    entries.append((face_label, _is_bold(face_label), path))
    return entries


@lru_cache(maxsize=64)
def resolve_font(font_name: str, bold: bool = True,
                 fallbacks: tuple[str, ...] = ("Arial", "Liberation Sans Narrow")) -> ResolvedFont:
    """Return an installed face; try configured fallbacks before failing."""
    candidates = (font_name, *fallbacks)
    entries = _windows_font_entries()
    if entries:
        for candidate in candidates:
            matches = [entry for entry in entries if _family_key(entry[0]) == _family_key(candidate)]
            if matches:
                matches.sort(key=lambda entry: (entry[1] != bold, entry[0].casefold()))
                return ResolvedFont(candidate, matches[0][2], font_name)

    # On systems with fontconfig (including Linux FFmpeg builds), ask the same
    # font resolver that libass commonly uses, then expose the resolved file.
    if os.name != "nt":
        for candidate in candidates:
            try:
                result = subprocess.run(
                    ["fc-match", "-f", "%{family}\n%{file}\n", candidate],
                    check=True, capture_output=True, text=True, timeout=5,
                )
                family, path_text = result.stdout.splitlines()[:2]
                path = Path(path_text)
                if path.is_file():
                    return ResolvedFont(family.split(",")[0].strip(), path, font_name)
            except (OSError, subprocess.SubprocessError, ValueError):
                continue

    raise FileNotFoundError(
        f"None of the configured font families is installed: {', '.join(candidates)}"
    )
