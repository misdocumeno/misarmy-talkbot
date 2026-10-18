"""``edge/`` namespace: Azure when configured, with edge-tts fallback on quota."""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.exceptions import (
    AzureQuotaExceeded,
    AzureSynthesisError,
)
from misarmy_talkbot.core.playback.tts.provider import MICROSOFT_PROSODY
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
    from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
        AzureTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.edge_backend import (
        EdgeTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings
    from misarmy_talkbot.infra.database.tts_usage import TtsUsageStore


class MicrosoftTtsRouter:
    """User-facing ``edge/`` backend: prefer Azure, fall back to edge-tts."""

    # Metering uses the leaf backend that actually produced audio.
    backend_name: str = 'azure'
    supports_native_prosody: bool = True
    supports_streaming: bool = True
    prosody_limits: ProsodyLimits = MICROSOFT_PROSODY

    def __init__(
        self,
        *,
        azure: AzureTtsProvider,
        edge: EdgeTtsProvider,
        settings: AzureSpeechSettings,
        usage_store: TtsUsageStore,
    ) -> None:
        self._azure = azure
        self._edge = edge
        self._settings = settings
        self._usage_store = usage_store
        self._last_backend: str = 'azure'

    @property
    def last_backend_name(self) -> str:
        """Backend that served the most recent ``synthesize`` call."""
        return self._last_backend

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        char_count = len(text)
        if await self._azure_over_cap(char_count):
            logger.warning(
                'azure_soft_cap_reached chars=%s cap=%s; falling_back=edge',
                char_count,
                self._settings.monthly_cap,
            )
            return await self._synthesize_edge(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )

        await self._maybe_warn_near_cap(char_count)

        try:
            audio = await self._azure.synthesize(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )
            self._last_backend = 'azure'
            self.backend_name = 'azure'
            return audio
        except (AzureQuotaExceeded, AzureSynthesisError) as exc:
            logger.warning('azure_tts_failed_fallback_edge error=%s', exc)
            return await self._synthesize_edge(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        """Stream Azure SDK MP3 chunks; fall back to edge-tts on quota/errors."""
        char_count = len(text)
        if await self._azure_over_cap(char_count):
            logger.warning(
                'azure_soft_cap_reached chars=%s cap=%s; falling_back=edge',
                char_count,
                self._settings.monthly_cap,
            )
            async for chunk in self._stream_edge(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            ):
                yield chunk
            return

        await self._maybe_warn_near_cap(char_count)
        produced = False
        try:
            self._last_backend = 'azure'
            self.backend_name = 'azure'
            async for chunk in self._azure.stream_synthesize(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            ):
                produced = True
                yield chunk
            return
        except (AzureQuotaExceeded, AzureSynthesisError) as exc:
            # Mid-stream fallback would mix Azure+edge bytes; only retry fresh.
            if produced:
                raise
            logger.warning('azure_tts_failed_fallback_edge error=%s', exc)
            async for chunk in self._stream_edge(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            ):
                yield chunk

    async def list_voice_ids(self) -> list[str]:
        """One Microsoft catalog (Azure ≈ edge-tts); no duplicate listing.

        Prefer Azure's official list; fall back to edge-tts if Azure is down.
        Runtime still falls back to edge-tts on soft-cap/errors without
        exposing a second set of the same ShortNames.
        """
        try:
            return await self._azure.list_voice_ids()
        except Exception:
            logger.exception('azure_list_voices_failed; using edge list')
            return await self._edge.list_voice_ids()

    async def _stream_edge(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        self._last_backend = 'edge'
        self.backend_name = 'edge'
        async for chunk in self._edge.stream_synthesize(
            text, voice_id=voice_id, pitch=pitch, speed=speed
        ):
            yield chunk

    async def _synthesize_edge(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        audio = await self._edge.synthesize(
            text, voice_id=voice_id, pitch=pitch, speed=speed
        )
        self._last_backend = 'edge'
        self.backend_name = 'edge'
        return audio

    async def _azure_over_cap(self, upcoming_chars: int) -> bool:
        cap = self._settings.monthly_cap
        if cap <= 0:
            return False
        used = await self._usage_store.azure_chars_this_utc_month(
            month_day=self._settings.quota_month_day,
            tz_name=self._settings.quota_tz,
        )
        return used + upcoming_chars > cap

    async def _maybe_warn_near_cap(self, upcoming_chars: int) -> None:
        cap = self._settings.monthly_cap
        if cap <= 0:
            return
        used = await self._usage_store.azure_chars_this_utc_month(
            month_day=self._settings.quota_month_day,
            tz_name=self._settings.quota_tz,
        )
        threshold = int(cap * self._settings.warn_ratio)
        if used < threshold <= used + upcoming_chars:
            logger.warning(
                'azure_soft_cap_warn used=%s cap=%s ratio=%s',
                used + upcoming_chars,
                cap,
                self._settings.warn_ratio,
            )
