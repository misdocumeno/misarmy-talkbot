"""``google/`` namespace: Cloud TTS when configured, gTTS scrape as fallback."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
    from misarmy_talkbot.core.playback.tts.providers.google_backend import (
        GoogleTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
        GoogleCloudTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.settings import (
        GoogleCloudTtsSettings,
    )
    from misarmy_talkbot.infra.database.tts_usage import TtsUsageStore


class GoogleTtsRouter:
    """Prefer Google Cloud; fall back to gTTS on quota, errors, or legacy ids."""

    backend_name: str = 'google_cloud'
    # Cloud applies prosody in-API (or ffmpeg for Chirp); gTTS uses ffmpeg here.
    supports_native_prosody: bool = True
    supports_streaming: bool = True

    def __init__(
        self,
        *,
        cloud: GoogleCloudTtsProvider,
        gtts: GoogleTtsProvider,
        settings: GoogleCloudTtsSettings,
        usage_store: TtsUsageStore,
    ) -> None:
        self._cloud = cloud
        self._gtts = gtts
        self._settings = settings
        self._usage_store = usage_store
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
        if await self._cloud_over_cap(len(text)):
            logger.warning(
                'google_cloud_soft_cap_reached chars=%s cap=%s; falling_back=gtts',
                len(text),
                self._settings.monthly_cap,
            )
            lang = _gtts_lang_from_cloud_voice(voice_id)
            return await self._synthesize_gtts(
                text, voice_id=lang, pitch=pitch, speed=speed
            )
        await self._maybe_warn_near_cap(len(text))
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

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        """Stream Chirp when under soft cap; else gTTS / one-shot Cloud."""
        if _looks_like_gtts_voice(voice_id):
            audio = await self._synthesize_gtts(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )
            if audio:
                yield audio
            return

        if await self._cloud_over_cap(len(text)):
            logger.warning(
                'google_cloud_soft_cap_reached chars=%s cap=%s; falling_back=gtts',
                len(text),
                self._settings.monthly_cap,
            )
            lang = _gtts_lang_from_cloud_voice(voice_id)
            audio = await self._synthesize_gtts(
                text, voice_id=lang, pitch=pitch, speed=speed
            )
            if audio:
                yield audio
            return

        await self._maybe_warn_near_cap(len(text))
        produced = False
        try:
            self._last_backend = 'google_cloud'
            self.backend_name = 'google_cloud'
            async for chunk in self._cloud.stream_synthesize(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            ):
                produced = True
                yield chunk
            return
        except Exception as exc:
            if produced:
                raise
            logger.warning(
                'google_cloud_failed_fallback_gtts error=%s voice=%s',
                exc,
                voice_id,
            )
            lang = _gtts_lang_from_cloud_voice(voice_id)
            audio = await self._synthesize_gtts(
                text, voice_id=lang, pitch=pitch, speed=speed
            )
            if audio:
                yield audio

    async def list_voice_ids(self) -> list[str]:
        """Full selectable catalog: every Cloud voice + legacy gTTS ids."""
        names: set[str] = set()
        try:
            names.update(await self._cloud.list_voice_ids())
        except Exception:
            logger.exception('google_cloud_list_voices_failed')
        try:
            names.update(await self._gtts.list_voice_ids())
        except Exception:
            logger.exception('gtts_list_voices_failed')
        if not names:
            raise RuntimeError('no google voices available from cloud or gtts')
        return sorted(names)

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

    async def _cloud_over_cap(self, upcoming_chars: int) -> bool:
        cap = self._settings.monthly_cap
        if cap <= 0:
            return False
        used = await self._usage_store.google_cloud_chars_this_utc_month()
        return used + upcoming_chars > cap

    async def _maybe_warn_near_cap(self, upcoming_chars: int) -> None:
        cap = self._settings.monthly_cap
        if cap <= 0:
            return
        used = await self._usage_store.google_cloud_chars_this_utc_month()
        threshold = int(cap * self._settings.warn_ratio)
        if used < threshold <= used + upcoming_chars:
            logger.warning(
                'google_cloud_soft_cap_warn used=%s cap=%s ratio=%s',
                used + upcoming_chars,
                cap,
                self._settings.warn_ratio,
            )


def _looks_like_gtts_voice(voice_id: str) -> bool:
    """``es-Spanish`` (gTTS) vs ``es-US-Standard-A`` / Chirp (Cloud)."""
    parts = voice_id.split('-')
    if len(parts) < 2:
        return True
    # Cloud names have a region code (2 letters) then a tier token.
    if len(parts[1]) == 2 and parts[1].isalpha() and parts[1].isupper():
        return False
    return True


def _gtts_lang_from_cloud_voice(voice_id: str) -> str:
    """Best-effort map ``es-US-Chirp3-HD-Aoede`` → ``es`` for gTTS fallback."""
    return voice_id.split('-', 1)[0]
