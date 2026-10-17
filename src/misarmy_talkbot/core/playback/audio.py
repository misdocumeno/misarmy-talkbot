"""One Discord message represented as TTS audio served over in-memory HTTP.

Synthesis runs in memory. Audio is published to ``AudioHttpServer`` so Lavalink
can ``GET`` an ``http://`` URL — no filesystem write on the hot path.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

from misarmy_talkbot.core.playback.tts.edits import apply_edits
from misarmy_talkbot.core.playback.tts.exceptions import NoTokensError
from misarmy_talkbot.core.playback.tts.registry import TtsRegistry
from misarmy_talkbot.core.playback.tts.stream import publish_tts_http
from misarmy_talkbot.infra.audio_http import AudioHttpServer
from misarmy_talkbot.infra.database.tts_usage import (
    TtsUsageStore,
    UsageRecord,
)
from misarmy_talkbot.infra.database.voice import get_voice
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    import discord

    from misarmy_talkbot.infra.audio_http import AudioStreamSession


class AudioState(Enum):
    PENDING = auto()
    PROCESSING = auto()
    READY = auto()
    FAILED = auto()


class AudioMessage:
    """Lifecycle for one chat message: text edits → TTS → HTTP URL for Lavalink.

    ``_generation`` invalidates in-flight ``process()`` after edit/delete so a
    stale synthesis cannot overwrite newer audio or resurrect a discarded stream.
    """

    content: str
    original: discord.Message
    timestamp: float
    state: AudioState
    track_url: str | None
    audio_bytes: int

    def __init__(
        self,
        message: discord.Message,
        *,
        http_server: AudioHttpServer | None = None,
        usage_store: TtsUsageStore | None = None,
    ) -> None:
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)
        self.track_url = None
        self.audio_bytes = 0
        self.fallback_notice_msgid: str | None = None
        self._session: AudioStreamSession | None = None
        self._generation = 0
        self._http = http_server or AudioHttpServer.instance()
        self._usage_store = usage_store or TtsUsageStore.instance()

    def invalidate(self) -> None:
        """Bump generation so any in-flight ``process()`` discards its result."""
        self._generation += 1

    async def process(self) -> None:
        """Generate TTS and publish it over HTTP; sets ``state`` accordingly."""
        generation = self._generation
        author = cast('discord.Member', self.original.author)
        self.state = AudioState.PROCESSING

        voice_settings = await get_voice(author)
        logger.debug('tts_processing %r %s', self.content, voice_settings)

        if self.content == '':
            if generation == self._generation:
                self.state = AudioState.READY
            return

        guild = self.original.guild
        guild_id = guild.id if guild is not None else 0
        error_message: str | None = None
        backend_name = 'unknown'
        latency_ms = 0
        session: AudioStreamSession | None = None

        try:
            published = await publish_tts_http(
                self.content,
                voice=voice_settings.voice,
                pitch=voice_settings.pitch,
                speed=voice_settings.speed,
                http_server=self._http,
            )
            backend_name = published.backend_name
            latency_ms = published.latency_ms
            session = published.session
            track_url = published.url
            audio_bytes = session.bytes_pushed
            self.fallback_notice_msgid = published.user_notice_msgid
        except asyncio.CancelledError:
            raise
        except NoTokensError:
            error_message = 'no_tokens'
            logger.warning(
                'tts_no_tokens content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            if generation != self._generation:
                return
            self.state = AudioState.FAILED
            await self._record_usage(
                guild_id=guild_id,
                user_id=author.id,
                provider=backend_name,
                voice=voice_settings.voice,
                chars=len(self.content),
                ok=False,
                latency_ms=latency_ms,
                error=error_message,
            )
            return
        except Exception as exc:
            error_message = str(exc)[:200]
            try:
                prefix, _voice_id = TtsRegistry.instance().parse_voice(
                    voice_settings.voice
                )
                backend_name = TtsRegistry.instance().backend_name_for(prefix)
            except Exception:
                backend_name = voice_settings.voice.split('/')[0]
            logger.warning(
                'tts_failed content=%r voice=%s error=%s',
                self.content,
                voice_settings.voice,
                error_message,
            )
            if generation != self._generation:
                return
            self.state = AudioState.FAILED
            await self._record_usage(
                guild_id=guild_id,
                user_id=author.id,
                provider=backend_name,
                voice=voice_settings.voice,
                chars=len(self.content),
                ok=False,
                latency_ms=latency_ms,
                error=error_message,
            )
            return

        assert session is not None  # set on success path above
        if generation != self._generation:
            self._http.discard(session.stream_id)
            return

        if audio_bytes <= 0:
            logger.warning(
                'tts_empty_audio content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            self._http.discard(session.stream_id)
            self.state = AudioState.FAILED
            await self._record_usage(
                guild_id=guild_id,
                user_id=author.id,
                provider=backend_name,
                voice=voice_settings.voice,
                chars=len(self.content),
                ok=False,
                latency_ms=latency_ms,
                error='empty_audio',
            )
            return

        self._session = session
        self.track_url = track_url
        self.audio_bytes = audio_bytes
        self.state = AudioState.READY
        await self._record_usage(
            guild_id=guild_id,
            user_id=author.id,
            provider=backend_name,
            voice=voice_settings.voice,
            chars=len(self.content),
            ok=True,
            latency_ms=latency_ms,
            error=None,
        )
        logger.debug(
            'tts_ready content=%r bytes=%s url=%s backend=%s',
            self.content,
            self.audio_bytes,
            self.track_url,
            backend_name,
        )

    async def _record_usage(
        self,
        *,
        guild_id: int,
        user_id: int,
        provider: str,
        voice: str,
        chars: int,
        ok: bool,
        latency_ms: int,
        error: str | None,
    ) -> None:
        await self._usage_store.record(
            UsageRecord(
                guild_id=guild_id,
                user_id=user_id,
                provider=provider,
                voice=voice,
                chars=chars,
                ok=ok,
                latency_ms=latency_ms,
                error=error,
            )
        )

    async def edit(self, message: discord.Message) -> None:
        """Reset to PENDING with new content; drop any published HTTP session."""
        self.invalidate()
        await self._discard_session()
        self.track_url = None
        self.audio_bytes = 0
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)

    async def cleanup(self) -> None:
        """Drop the HTTP session (idempotent) and invalidate in-flight work."""
        self.invalidate()
        await self._discard_session()
        self.track_url = None

    async def _discard_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            self._http.discard(session.stream_id)

    def __eq__(self, other: object) -> bool:
        from discord import Message  # local import to keep boot-order simple

        return (
            isinstance(other, AudioMessage) and self.original == other.original
        ) or (isinstance(other, Message) and self.original == other)

    def __hash__(self) -> int:
        return hash(self.original.id)

    def __repr__(self) -> str:
        return (
            f'AudioMessage(state={self.state.name}, content={self.content!r})'
        )
