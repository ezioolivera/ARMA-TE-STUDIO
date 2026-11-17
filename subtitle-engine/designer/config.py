"""Visual and segmentation settings for Subtitle Designer."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DesignerConfig:
    # Typography and ARMA-TE palette. ASS color strings are in &HAABBGGRR form.
    font_name: str = "Arial Narrow"
    fallback_font_names: tuple[str, ...] = ("Arial", "Liberation Sans Narrow")
    font_size: float = 58
    min_font_size: float = 34
    bold: bool = True
    primary_color: str = "&H00FFFFFF"  # white base text
    active_color: str = "&H00D5D737"  # turquoise (#37D7D5)
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"
    outline: float = 2.5
    shadow: float = 0.8

    # ASS alignment: 1-3 bottom, 4-6 middle, 7-9 top.
    alignment: int = 8
    margin_l: int = 90
    margin_r: int = 90
    margin_v: int = 72
    play_res_x: int = 1080
    play_res_y: int = 1920
    max_width_ratio: float = 0.85
    max_lines: int = 2
    vertical_position: float = 0.68
    safe_top_ratio: float = 0.05
    safe_bottom_ratio: float = 0.05
    # Normalized (left, top, right, bottom) box reserved for the ARMA-TE mark.
    watermark_box: tuple[float, float, float, float] | None = (0.35, 0.79, 0.65, 0.92)

    # Grouping targets. A pause or sentence boundary can produce shorter cues.
    min_words: int = 2
    max_words: int = 5
    max_chars: int = 48
    max_duration: float = 3.2
    pause_threshold: float = 0.48
    fade_in_ms: int = 80
    fade_out_ms: int = 100

    # Optional emphasis is bolded while remaining white in the base layer.
    highlight_words: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def for_video(cls, width: int, height: int, **overrides: object) -> "DesignerConfig":
        """Build a dimension-matched config with an orientation-aware default Y."""
        values: dict[str, object] = {
            "play_res_x": width,
            "play_res_y": height,
            "vertical_position": 0.68 if height >= width else 0.55,
        }
        values.update(overrides)
        return cls(**values)  # type: ignore[arg-type]

    def __post_init__(self) -> None:
        if not 1 <= self.min_words <= self.max_words:
            raise ValueError("Require 1 <= min_words <= max_words")
        if self.max_chars < 1 or self.max_duration <= 0 or self.pause_threshold < 0:
            raise ValueError("Segmentation limits must be positive")
        if not 0 < self.max_width_ratio <= 0.85 or self.max_lines != 2:
            raise ValueError("max_width_ratio must be in (0, 0.85] and max_lines is fixed at 2")
        if not 0 <= self.vertical_position <= 1:
            raise ValueError("vertical_position must be a normalized value from 0 to 1")
        if self.alignment != 8:
            raise ValueError("alignment is fixed to top-center so vertical placement is deterministic")
        if not 0 <= self.safe_top_ratio < 0.5 or not 0 <= self.safe_bottom_ratio < 0.5:
            raise ValueError("Safe margins must be normalized values below 0.5")
        if self.watermark_box is not None:
            left, top, right, bottom = self.watermark_box
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError("watermark_box must be normalized (left, top, right, bottom)")
        if self.font_size <= 0 or self.min_font_size <= 0 or self.min_font_size > self.font_size:
            raise ValueError("Font sizes must be positive and min_font_size <= font_size")
        if self.play_res_x <= 0 or self.play_res_y <= 0:
            raise ValueError("Font size and PlayRes dimensions must be positive")
        if self.fade_in_ms < 0 or self.fade_out_ms < 0:
            raise ValueError("Fade durations cannot be negative")

