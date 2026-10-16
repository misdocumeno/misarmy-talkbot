"""Env-backed TTS settings (Pydantic). Secrets stay in environment variables."""

from __future__ import annotations

import os

from pydantic import BaseModel, Field, field_validator


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

    Use a GCP API key with Cloud Text-to-Speech enabled, or leave empty to keep
    the free gTTS scrape path only.

    Voice listing defaults to the full Cloud catalog (Standard, WaveNet,
    Neural2, Chirp, …). Soft cap tracks Chirp/Standard billable use under
    ``google_cloud`` and falls back to gTTS when exceeded.
    """

    api_key: str = ''
    # all | Chirp3 | Standard | WaveNet | Neural2 — filters list_voices only.
    # Default ``all``: biggest selectable catalog (philosophy for every provider).
    voice_tier: str = 'all'
    monthly_cap: int = Field(
        default=1_000_000,
        ge=0,
        description='UTC calendar-month char soft cap for google_cloud; 0 = unlimited',
    )
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


def load_azure_speech_settings() -> AzureSpeechSettings:
    """Read Azure Speech settings from process env (empty key → disabled)."""
    raw_cap = os.getenv('AZURE_SPEECH_MONTHLY_CAP', '500000')
    try:
        monthly_cap = int(raw_cap)
    except ValueError:
        monthly_cap = 500_000
    return AzureSpeechSettings(
        key=os.getenv('AZURE_SPEECH_KEY', ''),
        region=os.getenv('AZURE_SPEECH_REGION', ''),
        monthly_cap=max(0, monthly_cap),
    )


def load_google_cloud_tts_settings() -> GoogleCloudTtsSettings:
    """Read Google Cloud TTS settings (empty key → gTTS only)."""
    raw_cap = os.getenv('GOOGLE_TTS_MONTHLY_CAP', '1000000')
    try:
        monthly_cap = int(raw_cap)
    except ValueError:
        monthly_cap = 1_000_000
    return GoogleCloudTtsSettings(
        api_key=os.getenv('GOOGLE_TTS_API_KEY', ''),
        voice_tier=os.getenv('GOOGLE_TTS_VOICE_TIER', 'all'),
        monthly_cap=max(0, monthly_cap),
    )
