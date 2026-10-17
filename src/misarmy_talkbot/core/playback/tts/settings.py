"""Env-backed TTS settings (Pydantic). Secrets stay in environment variables."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from misarmy_talkbot.core.playback.tts.google_billing import (
    default_cap_for_bucket,
)

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.google_billing import (
        GoogleBillingBucket,
    )


class AzureSpeechSettings(BaseModel):
    """Azure Cognitive Services Speech (neural TTS)."""

    key: str = ''
    region: str = ''
    monthly_cap: int = Field(
        default=500_000,
        ge=0,
        description='UTC calendar-month char soft cap; 0 = unlimited',
    )
    warn_ratio: float = Field(default=0.8, gt=0.0, le=1.0)

    @field_validator('key', 'region', mode='before')
    @classmethod
    def _strip_str(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def enabled(self) -> bool:
        return bool(self.key) and bool(self.region)


class GoogleCloudTtsSettings(BaseModel):
    """Google Cloud Text-to-Speech (official API).

    Soft caps are **per billing SKU bucket** (Standard+WaveNet share one free
    pool; Neural2; Chirp3). Studio / Gemini are not listed. Empty API key keeps
    the free gTTS scrape path only (still under the ``google/`` prefix).
    """

    api_key: str = ''
    # all | Chirp3 | Standard | WaveNet | Neural2 — filters list_voices only.
    voice_tier: str = 'all'
    # Opt-in: list Studio voices (1M free then $160/1M). Off by default.
    allow_studio: bool = False
    # Per-bucket UTC-month soft caps (0 = unlimited for that bucket).
    cap_standard_wavenet: int = Field(default=4_000_000, ge=0)
    cap_neural2: int = Field(default=1_000_000, ge=0)
    cap_chirp3: int = Field(default=1_000_000, ge=0)
    cap_studio: int = Field(default=1_000_000, ge=0)
    warn_ratio: float = Field(default=0.8, gt=0.0, le=1.0)

    @field_validator('api_key', 'voice_tier', mode='before')
    @classmethod
    def _strip_str(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    @property
    def monthly_cap(self) -> int:
        """Legacy single-number view (min of paid-tier buckets) for reports."""
        caps = [
            c
            for c in (
                self.cap_standard_wavenet,
                self.cap_neural2,
                self.cap_chirp3,
            )
            if c > 0
        ]
        return min(caps) if caps else 0

    def cap_for_bucket(self, bucket: GoogleBillingBucket) -> int:
        if bucket == 'standard_wavenet':
            return self.cap_standard_wavenet
        if bucket == 'neural2':
            return self.cap_neural2
        if bucket == 'chirp3':
            return self.cap_chirp3
        if bucket == 'studio':
            return self.cap_studio if self.allow_studio else 0
        # unsupported (Gemini, InstantCustom, …): never hit Cloud
        return 0


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return default


def load_azure_speech_settings() -> AzureSpeechSettings:
    """Read Azure Speech settings from process env (empty key → disabled)."""
    return AzureSpeechSettings(
        key=os.getenv('AZURE_SPEECH_KEY', ''),
        region=os.getenv('AZURE_SPEECH_REGION', ''),
        monthly_cap=_env_int('AZURE_SPEECH_MONTHLY_CAP', 500_000),
    )


def load_google_cloud_tts_settings() -> GoogleCloudTtsSettings:
    """Read Google Cloud TTS settings (empty key → gTTS only).

    ``GOOGLE_TTS_MONTHLY_CAP`` (legacy) still sets Neural2 + Chirp3 caps when
    the per-bucket env vars are unset. Standard+WaveNet default to 4M.
    """
    legacy = os.getenv('GOOGLE_TTS_MONTHLY_CAP')
    legacy_cap = (
        _env_int('GOOGLE_TTS_MONTHLY_CAP', 1_000_000)
        if legacy is not None and legacy.strip()
        else None
    )
    neural_default = (
        legacy_cap
        if legacy_cap is not None
        else default_cap_for_bucket('neural2')
    )
    chirp_default = (
        legacy_cap
        if legacy_cap is not None
        else default_cap_for_bucket('chirp3')
    )
    allow_studio = os.getenv(
        'GOOGLE_TTS_ALLOW_STUDIO', ''
    ).strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
    }
    return GoogleCloudTtsSettings(
        api_key=os.getenv('GOOGLE_TTS_API_KEY', ''),
        voice_tier=os.getenv('GOOGLE_TTS_VOICE_TIER', 'all'),
        allow_studio=allow_studio,
        cap_standard_wavenet=_env_int(
            'GOOGLE_TTS_CAP_STANDARD_WAVENET',
            default_cap_for_bucket('standard_wavenet'),
        ),
        cap_neural2=_env_int('GOOGLE_TTS_CAP_NEURAL2', neural_default),
        cap_chirp3=_env_int('GOOGLE_TTS_CAP_CHIRP3', chirp_default),
        cap_studio=_env_int(
            'GOOGLE_TTS_CAP_STUDIO', default_cap_for_bucket('studio')
        ),
    )
