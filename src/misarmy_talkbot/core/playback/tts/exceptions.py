"""TTS domain errors."""

from __future__ import annotations


class TtsError(Exception):
    """Base class for synthesis failures."""


class NoTokensError(TtsError):
    """gTTS reported no speakable tokens for the input text."""


class AzureQuotaExceeded(TtsError):
    """Azure monthly soft cap reached (may trigger edge-tts fallback)."""


class AzureSynthesisError(TtsError):
    """Azure Speech REST call failed."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ProsodyValidationError(ValueError):
    """Pitch/speed outside the provider's allowed range (config-time)."""


class VoiceNotAllowedError(TtsError):
    """Voice id is known but not enabled for this deployment (e.g. Studio)."""
