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
