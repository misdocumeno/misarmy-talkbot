"""Google Cloud Text-to-Speech REST (Standard / WaveNet / Neural2)."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import aiohttp

from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.settings import (
        GoogleCloudTtsSettings,
    )

# Official AudioConfig ranges:
# https://cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize
# speakingRate ∈ [0.25, 4.0], pitch ∈ [-20.0, 20.0] semitones.
GOOGLE_CLOUD_PROSODY = ProsodyLimits(
    pitch_min=0.25, pitch_max=3.0, speed_min=0.25, speed_max=4.0
)

_SYNTHESIZE_URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
_VOICES_URL = 'https://texttospeech.googleapis.com/v1/voices'


def _pitch_to_semitones(pitch: float) -> float:
    """Map multiplier (1.0 = default) to Google semitone offset.

    Roughly ±1.0 on the multiplier → ±12 st (one octave). Clamped to API max.
    """
    semitones = (pitch - 1.0) * 12.0
    return max(-20.0, min(20.0, semitones))


class GoogleCloudTtsProvider:
    backend_name: str = 'google_cloud'
    supports_native_prosody: bool = True
    # Google StreamingSynthesize is Chirp 3 HD only (not Standard/WaveNet/Neural2):
    # https://cloud.google.com/text-to-speech/docs/create-audio-text-streaming
    # We use Standard (free tier) → one-shot REST into the in-memory HTTP bridge.
    supports_streaming: bool = False
    prosody_limits: ProsodyLimits = GOOGLE_CLOUD_PROSODY

    def __init__(self, settings: GoogleCloudTtsSettings) -> None:
        if not settings.enabled:
            raise ValueError('Google Cloud TTS settings are not enabled')
        self._settings = settings
        self._voices_cache: list[str] | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        language_code = _language_code_from_voice(voice_id)
        speaking_rate = max(0.25, min(4.0, speed))
        pitch_st = _pitch_to_semitones(pitch)
        body = {
            'input': {'text': text},
            'voice': {'languageCode': language_code, 'name': voice_id},
            'audioConfig': {
                'audioEncoding': 'MP3',
                'speakingRate': speaking_rate,
                'pitch': pitch_st,
            },
        }
        params = {'key': self._settings.api_key}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _SYNTHESIZE_URL, params=params, json=body
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    detail = str(payload)[:200]
                    raise RuntimeError(
                        f'Google Cloud TTS HTTP {response.status}: {detail}'
                    )
        encoded = payload.get('audioContent')
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError('Google Cloud TTS returned empty audioContent')
        return base64.b64decode(encoded)

    async def list_voice_ids(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        params = {'key': self._settings.api_key}
        async with aiohttp.ClientSession() as session:
            async with session.get(_VOICES_URL, params=params) as response:
                if response.status != 200:
                    detail = (await response.text())[:200]
                    raise RuntimeError(
                        f'Google Cloud voices HTTP {response.status}: {detail}'
                    )
                payload = await response.json(content_type=None)
        names: list[str] = []
        tier = self._settings.voice_tier.lower()
        for entry in payload.get('voices', []):
            if not isinstance(entry, dict):
                continue
            name = entry.get('name')
            if not isinstance(name, str) or not name:
                continue
            if tier and tier != 'all' and tier not in name.lower():
                continue
            names.append(name)
        self._voices_cache = sorted(set(names))
        logger.info(
            'google_cloud_voices_cached count=%s tier=%s',
            len(self._voices_cache),
            self._settings.voice_tier,
        )
        return list(self._voices_cache)


def _language_code_from_voice(voice_id: str) -> str:
    """``es-US-Standard-A`` → ``es-US``."""
    parts = voice_id.split('-')
    if len(parts) >= 2:
        return f'{parts[0]}-{parts[1]}'
    return voice_id
