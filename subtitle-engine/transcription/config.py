"""Validated configuration for local Faster-Whisper inference."""

from dataclasses import dataclass

SUPPORTED_MODELS = ("tiny", "base", "small", "medium")


@dataclass(frozen=True, slots=True)
class TranscriptionConfig:
    """Options for a CPU int8 transcription run.

    ``language=None`` enables Whisper's language detection. Use ``"es"`` to
    hint Spanish audio explicitly. ``base`` is a useful quick-test model;
    ``small`` is the default quality/speed choice.
    """

    model_size: str = "small"
    language: str | None = None
    device: str = "cpu"
    compute_type: str = "int8"
    beam_size: int = 5

    def __post_init__(self) -> None:
        if self.model_size not in SUPPORTED_MODELS:
            choices = ", ".join(SUPPORTED_MODELS)
            raise ValueError(f"model_size must be one of: {choices}")
        if self.device != "cpu" or self.compute_type != "int8":
            raise ValueError("This engine currently supports CPU with int8 only")
        if self.language is not None and not self.language.strip():
            raise ValueError("language must be None or a non-empty language code")
        if self.beam_size < 1:
            raise ValueError("beam_size must be at least 1")
