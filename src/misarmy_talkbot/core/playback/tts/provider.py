"""TTS provider protocol and shared prosody limits (Pydantic-validated)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, model_validator


class ProsodyLimits(BaseModel):
    """Allowed pitch/speed multipliers for a provider (1.0 = unchanged)."""

    pitch_min: float = Field(default=0.5, gt=0.0)
    pitch_max: float = Field(default=1.5, gt=0.0)
    speed_min: float = Field(default=0.5, gt=0.0)
    speed_max: float = Field(default=2.0, gt=0.0)

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
    """gTTS pitch/speed must keep ffmpeg atempo inside [0.5, 6.0]."""

    atempo_min: float = 0.5
    atempo_max: float = 6.0

    def validate_values(self, *, pitch: float, speed: float) -> None:
        super().validate_values(pitch=pitch, speed=speed)
        atempo = speed / pitch
        if not (self.atempo_min <= atempo <= self.atempo_max):
            raise ValueError(
                f'pitch/speed combination yields atempo={atempo:.3f}; '
                f'must be between {self.atempo_min} and {self.atempo_max}'
            )


MICROSOFT_PROSODY = ProsodyLimits(
    pitch_min=0.5, pitch_max=1.5, speed_min=0.5, speed_max=2.0
)
GOOGLE_PROSODY = GoogleProsodyLimits(
    pitch_min=0.5, pitch_max=2.0, speed_min=0.5, speed_max=3.0
)


@runtime_checkable
class TtsProvider(Protocol):
    """One TTS backend. User-facing namespace may differ from backend name."""

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
