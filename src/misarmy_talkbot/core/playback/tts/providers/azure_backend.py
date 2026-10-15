"""Azure Cognitive Services Speech REST (neural voices)."""

from __future__ import annotations

import time
import xml.sax.saxutils
from typing import TYPE_CHECKING

import aiohttp

from misarmy_talkbot.core.playback.tts.exceptions import (
    AzureQuotaExceeded,
    AzureSynthesisError,
)
from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)
from misarmy_talkbot.core.playback.tts.provider import MICROSOFT_PROSODY
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
    from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings


_OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3'
# Issued tokens are valid ~10 minutes; refresh a bit early.
_TOKEN_TTL_SECONDS = 9 * 60


class AzureTtsProvider:
    backend_name: str = 'azure'
    supports_native_prosody: bool = True
    prosody_limits: ProsodyLimits = MICROSOFT_PROSODY

    def __init__(self, settings: AzureSpeechSettings) -> None:
        if not settings.enabled:
            raise ValueError('Azure Speech settings are not enabled')
        self._settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._voices_cache: list[str] | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        ssml = _build_ssml(
            text,
            voice_id=voice_id,
            pitch=pitch,
            speed=speed,
        )
        token = await self._ensure_token()
        url = (
            f'https://{self._settings.region}.tts.speech.microsoft.com'
            '/cognitiveservices/v1'
        )
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/ssml+xml',
            'X-Microsoft-OutputFormat': _OUTPUT_FORMAT,
            'User-Agent': 'misarmy-talkbot',
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=ssml.encode('utf-8'), headers=headers
            ) as response:
                body = await response.read()
                if response.status == 200:
                    return body
                if response.status in (401, 403, 429):
                    # Auth/throttle → quota-ish for edge fallback.
                    detail = body.decode('utf-8', errors='replace')[:200]
                    logger.warning(
                        'azure_tts_quotaish status=%s detail=%s',
                        response.status,
                        detail,
                    )
                    raise AzureQuotaExceeded(
                        f'Azure Speech HTTP {response.status}: {detail}'
                    )
                detail = body.decode('utf-8', errors='replace')[:200]
                raise AzureSynthesisError(
                    f'Azure Speech HTTP {response.status}: {detail}',
                    status=response.status,
                )

    async def list_voice_ids(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        token = await self._ensure_token()
        url = (
            f'https://{self._settings.region}.tts.speech.microsoft.com'
            '/cognitiveservices/voices/list'
        )
        headers = {'Authorization': f'Bearer {token}'}
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, headers=headers) as response,
        ):
            if response.status != 200:
                detail = (await response.text())[:200]
                raise AzureSynthesisError(
                    f'Azure voices list HTTP {response.status}: {detail}',
                    status=response.status,
                )
            payload = await response.json(content_type=None)
        names: list[str] = []
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                short_name = entry.get('ShortName')
                if isinstance(short_name, str) and short_name:
                    names.append(short_name)
        self._voices_cache = sorted(set(names))
        return list(self._voices_cache)

    async def _ensure_token(self) -> str:
        now = time.monotonic()
        if self._token is not None and now < self._token_expires_at:
            return self._token
        url = (
            f'https://{self._settings.region}.api.cognitive.microsoft.com'
            '/sts/v1.0/issueToken'
        )
        headers = {
            'Ocp-Apim-Subscription-Key': self._settings.key,
            'Content-Length': '0',
        }
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, headers=headers) as response,
        ):
            if response.status != 200:
                detail = (await response.text())[:200]
                raise AzureSynthesisError(
                    f'Azure token HTTP {response.status}: {detail}',
                    status=response.status,
                )
            token = await response.text()
        self._token = token
        self._token_expires_at = now + _TOKEN_TTL_SECONDS
        return token


def _build_ssml(
    text: str, *, voice_id: str, pitch: float, speed: float
) -> str:
    # xml:lang is required; ShortName locale prefix is good enough for neural.
    locale = '-'.join(voice_id.split('-')[:2]) if '-' in voice_id else 'en-US'
    escaped = xml.sax.saxutils.escape(text)
    rate = microsoft_rate_string(speed)
    pitch_str = microsoft_pitch_string(pitch)
    return (
        "<speak version='1.0' xml:lang="
        f"'{locale}' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts'>"
        f"<voice name='{xml.sax.saxutils.escape(voice_id)}'>"
        f"<prosody rate='{rate}' pitch='{pitch_str}'>{escaped}</prosody>"
        '</voice></speak>'
    )
