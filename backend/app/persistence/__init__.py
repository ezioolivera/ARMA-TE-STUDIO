"""Persistence contracts and autosave preparation."""

from .autosave import (
    AutosaveCoordinator,
    AutosaveFailure,
    AutosavePolicy,
    AutosaveShutdownError,
    AutosaveWriteError,
)

__all__ = [
    "AutosaveCoordinator",
    "AutosaveFailure",
    "AutosavePolicy",
    "AutosaveShutdownError",
    "AutosaveWriteError",
]
