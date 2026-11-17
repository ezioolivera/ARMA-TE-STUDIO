"""Real font width measurement using Pillow or native Windows GDI metrics."""

from __future__ import annotations

import ctypes
import os
from typing import Any

from .fonts import ResolvedFont


class TextMeasurer:
    """Measure rendered glyph advances for a resolved font family."""

    def __init__(self, font: ResolvedFont, font_size: float, bold: bool = True) -> None:
        self.font = font
        self.font_size = font_size
        self.bold = bold
        self._pillow: Any | None = None
        self._dc: Any | None = None
        self._gdi: Any | None = None
        self._font_handles: dict[int, tuple[Any, Any]] = {}
        self._pillow_fonts: dict[int, Any] = {}
        try:
            from PIL import ImageFont
            self._image_font = ImageFont
        except ImportError:
            self._image_font = None
        if self._image_font is None:
            if os.name != "nt":
                raise RuntimeError(
                    "Install Pillow for font metrics outside Windows; Windows GDI is used otherwise"
                )
            self._init_gdi()

    def _init_gdi(self) -> None:
        self._gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        self._gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        self._gdi.CreateCompatibleDC.restype = ctypes.c_void_p
        self._gdi.CreateFontW.argtypes = (
            [ctypes.c_int] * 5 + [ctypes.c_uint] * 8 + [ctypes.c_wchar_p]
        )
        self._gdi.CreateFontW.restype = ctypes.c_void_p
        self._gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._gdi.SelectObject.restype = ctypes.c_void_p
        self._gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        self._gdi.DeleteObject.restype = ctypes.c_int
        self._gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        self._gdi.DeleteDC.restype = ctypes.c_int
        self._gdi.GetTextExtentPoint32W.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                                     ctypes.c_int, ctypes.c_void_p]
        self._gdi.GetTextExtentPoint32W.restype = ctypes.c_int
        self._dc = self._gdi.CreateCompatibleDC(None)
        if not self._dc:
            raise ctypes.WinError(ctypes.get_last_error())

    def _gdi_font(self, size: int) -> tuple[Any, Any]:
        if size not in self._font_handles:
            handle = self._gdi.CreateFontW(
                -size, 0, 0, 0, 700 if self.bold else 400, 0, 0, 0,
                1, 0, 0, 5, 0, self.font.family,
            )
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            old = self._gdi.SelectObject(self._dc, handle)
            self._font_handles[size] = (handle, old)
        return self._font_handles[size]

    def measure(self, text: str, font_size: float | None = None) -> float:
        size = max(1, round(font_size or self.font_size))
        if self._image_font is not None:
            if size not in self._pillow_fonts:
                self._pillow_fonts[size] = self._image_font.truetype(str(self.font.path), size)
            return float(self._pillow_fonts[size].getlength(text))

        handle, _ = self._gdi_font(size)
        self._gdi.SelectObject(self._dc, handle)

        class Size(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        measured = Size()
        utf16_length = len(text.encode("utf-16-le")) // 2
        ok = self._gdi.GetTextExtentPoint32W(
            self._dc, text, utf16_length, ctypes.byref(measured)
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return float(measured.cx)

    def close(self) -> None:
        if self._gdi is not None:
            for handle, old in self._font_handles.values():
                self._gdi.SelectObject(self._dc, old)
                self._gdi.DeleteObject(handle)
            if self._dc:
                self._gdi.DeleteDC(self._dc)
        self._font_handles.clear()

    def __enter__(self) -> "TextMeasurer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
