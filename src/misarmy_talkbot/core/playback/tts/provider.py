"""TTS provider protocol and documented prosody limits (Pydantic-validated).

Prosody = pitch and speaking-rate multipliers (``1.0`` = the voice default).
Validated at config time (``/pitch``, ``/speed``, preset load), not per TTS call.

Sources (not guesswork):

* **Azure / Edge (Microsoft neural SSML)** — Learn docs for ``<prosody>``:
  pitch must stay within **0.5–1.5×** the original; rate within **0.5–2×**.
  https://learn.microsoft.com/azure/ai-services/speech-service/speech-synthesis-markup-voice#adjust-prosody
  ``edge-tts`` sends the same rate/pitch strings to Microsoft's Read Aloud stack,
  so it shares these bounds. We do not invent wider ranges without a live
  probe that shows the service accepting them.

* **gTTS + ffmpeg** — gTTS has no prosody API. Pitch/speed are baked with
  ``asetrate`` / ``atempo``. ffmpeg documents each ``atempo`` factor in
  **[0.5, 100.0]** and explicitly allows daisy-chaining for products outside
  that window: https://ffmpeg.org/ffmpeg-filters.html#atempo
  So any ``pitch > 0`` and ``speed > 0`` is expressible; we reject only
  non-positive multipliers (undefined sample rate / tempo). Extreme
  ``asetrate`` values are not capped here until a measured failure mode exists.

* **Google Cloud TTS** — ``AudioConfig.speakingRate`` ∈ **[0.25, 4.0]** and
  ``pitch`` ∈ **[-20, 20]** semitones (API). User-facing multipliers map pitch
  via ``(pitch - 1) * 12`` st; see ``GOOGLE_CLOUD_PROSODY`` in the cloud backend.
  https://cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator


class ProsodyLimits(BaseModel):
    """Pitch/speed bounds for a provider (``1.0`` = unchanged)."""

    pitch_min: float = Field(gt=0.0)
    pitch_max: float = Field(gt=0.0)
    speed_min: float = Field(gt=0.0)
    speed_max: float = Field(gt=0.0)

    @model_validator(mode='after')
    def _ordered_bounds(self) -> ProsodyLimits:
        if self.pitch_min > self.pitch_max:
            raise ValueError('pitch_min must be <= pitch_max')
        if self.speed_min > self.speed_max:
            raise ValueError('speed_min must be <= speed_max')
        return self

    def validate_values(self, *, pitch: float, speed: float) -> None:
        if not (self.pitch_min <= pitch <= self.pitch_max):
            raise ValueError(
                f'pitch must be between {self.pitch_min} and {self.pitch_max} '
                f'(got {pitch})'
            )
        if not (self.speed_min <= speed <= self.speed_max):
            raise ValueError(
                f'speed must be between {self.speed_min} and {self.speed_max} '
                f'(got {speed})'
            )


class GoogleProsodyLimits(ProsodyLimits):
    """gTTS has no prosody API; bounds come from ffmpeg filter math only."""

    def validate_values(self, *, pitch: float, speed: float) -> None:
        # Upper bounds on the model are placeholders for "unbounded positive";
        # the real rule is pitch > 0 and speed > 0 (see module docstring).
        if pitch <= 0.0:
            raise ValueError(f'pitch must be > 0 (got {pitch})')
        if speed <= 0.0:
            raise ValueError(f'speed must be > 0 (got {speed})')
        atempo = speed / pitch
        if atempo <= 0.0:
            raise ValueError(
                f'pitch/speed combination yields atempo={atempo:.3f}; '
                'must be > 0 (ffmpeg atempo)'
            )


# Azure Speech SSML <prosody> documented multiplier windows (also used by edge).
MICROSOFT_PROSODY = ProsodyLimits(
    pitch_min=0.5, pitch_max=1.5, speed_min=0.5, speed_max=2.0
)

# gTTS: no API caps. Placeholders keep the BaseModel valid; validate_values
# only enforces positivity + expressible atempo (chained per ffmpeg docs).
GOOGLE_PROSODY = GoogleProsodyLimits(
    pitch_min=0.01, pitch_max=1_000.0, speed_min=0.01, speed_max=1_000.0
)


@runtime_checkable
class TtsProvider(Protocol):
    """Structural interface for one TTS backend (duck typing + type checkers).

    User-facing voice prefixes (``edge/``, ``google/``) may map to a different
    ``backend_name`` used for metering (e.g. ``edge/`` → ``azure`` or ``edge``).
    """

    @property
    def backend_name(self) -> str:
        """Metering id: azure, edge, google, …"""
        ...

    @property
    def supports_native_prosody(self) -> bool:
        """True when pitch/speed go in the API; False means ffmpeg post-process."""
        ...

    @property
    def prosody_limits(self) -> ProsodyLimits: ...

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        """Return MP3 bytes for ``text`` using ``voice_id`` (no provider prefix)."""
        ...

    async def list_voice_ids(self) -> list[str]:
        """Short names without namespace prefix (e.g. ``es-AR-ElenaNeural``)."""
        ...
