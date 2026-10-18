"""``google/`` namespace: Cloud TTS when configured, gTTS scrape as fallback."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.core.playback.tts.exceptions import VoiceNotAllowedError
from misarmy_talkbot.core.playback.tts.google_billing import (
    classify_google_voice,
)
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
    """Prefer Google Cloud; fall back to gTTS on quota/errors (not listed)."""

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
        self._pending_user_notice_msgid: str | None = None

    @property
    def last_backend_name(self) -> str:
        return self._last_backend

    @property
    def prosody_limits(self) -> ProsodyLimits:
        return self._cloud.prosody_limits

    def take_user_notice_msgid(self) -> str | None:
        """Consume a one-shot gettext id for Discord fallback embeds."""
        notice = self._pending_user_notice_msgid
        self._pending_user_notice_msgid = None
        return notice

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
        self._reject_disallowed_cloud_voice(voice_id)
        if await self._cloud_over_cap(voice_id, len(text)):
            bucket = classify_google_voice(voice_id)
            logger.warning(
                'google_cloud_soft_cap_reached bucket=%s chars=%s cap=%s; '
                'falling_back=gtts',
                bucket,
                len(text),
                self._settings.cap_for_bucket(bucket),
            )
            return await self._fallback_gtts_from_cloud(
                text,
                voice_id=voice_id,
                pitch=pitch,
                speed=speed,
                notice_msgid='tts_fallback_gtts_soft_cap',
            )
        await self._maybe_warn_near_cap(voice_id, len(text))
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
            return await self._fallback_gtts_from_cloud(
                text,
                voice_id=voice_id,
                pitch=pitch,
                speed=speed,
                notice_msgid='tts_fallback_gtts_error',
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

        self._reject_disallowed_cloud_voice(voice_id)
        if await self._cloud_over_cap(voice_id, len(text)):
            bucket = classify_google_voice(voice_id)
            logger.warning(
                'google_cloud_soft_cap_reached bucket=%s chars=%s cap=%s; '
                'falling_back=gtts',
                bucket,
                len(text),
                self._settings.cap_for_bucket(bucket),
            )
            audio = await self._fallback_gtts_from_cloud(
                text,
                voice_id=voice_id,
                pitch=pitch,
                speed=speed,
                notice_msgid='tts_fallback_gtts_soft_cap',
            )
            if audio:
                yield audio
            return

        await self._maybe_warn_near_cap(voice_id, len(text))
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
            audio = await self._fallback_gtts_from_cloud(
                text,
                voice_id=voice_id,
                pitch=pitch,
                speed=speed,
                notice_msgid='tts_fallback_gtts_error',
            )
            if audio:
                yield audio

    def _reject_disallowed_cloud_voice(self, voice_id: str) -> None:
        """Hard-deny Studio/Gemini/etc. unless explicitly enabled — every request.

        Catalog filtering is not enough: a stale DB row must never hit a paid
        SKU. Future premium tiers can flip ``allow_studio`` (or finer flags).
        """
        bucket = classify_google_voice(voice_id)
        if bucket == 'studio' and not self._settings.allow_studio:
            raise VoiceNotAllowedError(
                f'Studio voice disabled: {voice_id!r} '
                '(set GOOGLE_TTS_ALLOW_STUDIO=1 to enable)'
            )
        if bucket == 'unsupported':
            raise VoiceNotAllowedError(
                f'Google Cloud voice not supported here: {voice_id!r}'
            )

    async def list_voice_ids(self) -> list[str]:
        """Cloud catalog only (Standard/WaveNet/Neural2/Chirp3; no Studio).

        gTTS is a runtime substitute only — never listed as a selectable set.
        """
        try:
            return await self._cloud.list_voice_ids()
        except Exception:
            logger.exception('google_cloud_list_voices_failed; empty catalog')
            return []

    async def _fallback_gtts_from_cloud(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
        notice_msgid: str,
    ) -> bytes:
        self._pending_user_notice_msgid = notice_msgid
        lang = _gtts_lang_from_cloud_voice(voice_id)
        return await self._synthesize_gtts(
            text, voice_id=lang, pitch=pitch, speed=speed
        )

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

    async def _cloud_over_cap(
        self, voice_id: str, upcoming_chars: int
    ) -> bool:
        bucket = classify_google_voice(voice_id)
        cap = self._settings.cap_for_bucket(bucket)
        # Unsupported / Studio: never hit Cloud (cap 0 → over).
        if cap <= 0:
            return True
        used = await self._usage_store.google_cloud_chars_this_utc_month(
            bucket=bucket,
            month_day=self._settings.quota_month_day,
            tz_name=self._settings.quota_tz,
        )
        return used + upcoming_chars > cap

    async def _maybe_warn_near_cap(
        self, voice_id: str, upcoming_chars: int
    ) -> None:
        bucket = classify_google_voice(voice_id)
        cap = self._settings.cap_for_bucket(bucket)
        if cap <= 0:
            return
        used = await self._usage_store.google_cloud_chars_this_utc_month(
            bucket=bucket,
            month_day=self._settings.quota_month_day,
            tz_name=self._settings.quota_tz,
        )
        threshold = int(cap * self._settings.warn_ratio)
        if used < threshold <= used + upcoming_chars:
            logger.warning(
                'google_cloud_soft_cap_warn bucket=%s used=%s cap=%s ratio=%s',
                bucket,
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
