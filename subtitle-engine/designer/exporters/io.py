"""Shared UTF-8 destination writer."""

from pathlib import Path


def write_text(content: str, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return path
