"""``google/`` namespace: Cloud TTS when configured, gTTS scrape as fallback."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
    from misarmy_talkbot.core.playback.tts.providers.google_backend import (
        GoogleTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
        GoogleCloudTtsProvider,
    )


class GoogleTtsRouter:
    """Prefer Google Cloud; fall back to gTTS on errors or legacy voice ids."""

    backend_name: str = 'google_cloud'
    # Cloud applies prosody in-API; gTTS fallback applies ffmpeg inside router.
    supports_native_prosody: bool = True
    supports_streaming: bool = False

    def __init__(
        self,
        *,
        cloud: GoogleCloudTtsProvider,
        gtts: GoogleTtsProvider,
    ) -> None:
        self._cloud = cloud
        self._gtts = gtts
        self._last_backend: str = 'google_cloud'

    @property
    def last_backend_name(self) -> str:
        return self._last_backend

    @property
    def prosody_limits(self) -> ProsodyLimits:
        return self._cloud.prosody_limits

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        if _looks_like_gtts_voice(voice_id):
            return await self._synthesize_gtts(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )
        try:
            audio = await self._cloud.synthesize(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )
            self._last_backend = 'google_cloud'
            self.backend_name = 'google_cloud'
            return audio
        except Exception as exc:
            logger.warning(
                'google_cloud_failed_fallback_gtts error=%s voice=%s',
                exc,
                voice_id,
            )
            lang = _gtts_lang_from_cloud_voice(voice_id)
            return await self._synthesize_gtts(
                text, voice_id=lang, pitch=pitch, speed=speed
            )

    async def list_voice_ids(self) -> list[str]:
        try:
            return await self._cloud.list_voice_ids()
        except Exception:
            logger.exception('google_cloud_list_voices_failed; using gtts')
            return await self._gtts.list_voice_ids()

    async def _synthesize_gtts(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        audio = await self._gtts.synthesize(
            text, voice_id=voice_id, pitch=pitch, speed=speed
        )
        if pitch != 1.0 or speed != 1.0:
            buffer = await apply_ffmpeg_prosody(
                io.BytesIO(audio), pitch=pitch, speed=speed
            )
            audio = buffer.getvalue()
        self._last_backend = 'google'
        self.backend_name = 'google'
        return audio


def _looks_like_gtts_voice(voice_id: str) -> bool:
    """``es-Spanish`` (gTTS) vs ``es-US-Standard-A`` (Cloud)."""
    parts = voice_id.split('-')
    if len(parts) < 2:
        return True
    # Cloud names have a region code (2 letters) then a tier token.
    if len(parts[1]) == 2 and parts[1].isalpha() and parts[1].isupper():
        return False
    return True


def _gtts_lang_from_cloud_voice(voice_id: str) -> str:
    """Best-effort map ``es-US-Standard-A`` → ``es`` for gTTS fallback."""
    return voice_id.split('-', 1)[0]
