"""Feed TTS audio into an ``AudioHttpServer`` session (stream or one-shot)."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, cast

from misarmy_talkbot.core.playback.tts.registry import TtsRegistry
from misarmy_talkbot.core.playback.tts.synthesize import synthesize_mp3
from misarmy_talkbot.infra.audio_http import AudioHttpServer
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.infra.audio_http import AudioStreamSession


class StreamPublishResult:
    """HTTP URL Lavalink can load, plus metering metadata."""

    __slots__ = ('session', 'url', 'backend_name', 'latency_ms', 'chars')

    def __init__(
        self,
        *,
        session: AudioStreamSession,
        url: str,
        backend_name: str,
        latency_ms: int,
        chars: int,
    ) -> None:
        self.session = session
        self.url = url
        self.backend_name = backend_name
        self.latency_ms = latency_ms
        self.chars = chars


async def publish_tts_http(
    text: str,
    *,
    voice: str,
    pitch: float,
    speed: float,
    registry: TtsRegistry | None = None,
    http_server: AudioHttpServer | None = None,
) -> StreamPublishResult:
    """Synthesize into an in-memory HTTP stream; return when first byte is ready.

    Providers with ``stream_synthesize`` push chunks as they arrive. Others
    synthesize fully in RAM and publish one buffer (still no disk).
    """
    voice_registry = registry or TtsRegistry.instance()
    server = http_server or AudioHttpServer.instance()
    prefix, voice_id = voice_registry.parse_voice(voice)
    provider = voice_registry.get(prefix)

    session = server.create_session()
    started = time.perf_counter()
    stream_fn = getattr(provider, 'stream_synthesize', None)

    if callable(stream_fn):
        chunks = cast(
            'AsyncIterator[bytes]',
            stream_fn(text, voice_id=voice_id, pitch=pitch, speed=speed),
        )
        asyncio.create_task(
            _run_feeder(server, session, chunks),
            name=f'tts_http_feed_{session.stream_id}',
        )
        await session.first_byte.wait()
        if session.error and session.bytes_pushed == 0:
            raise RuntimeError(session.error)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return StreamPublishResult(
            session=session,
            url=server.url_for(session.stream_id),
            backend_name=voice_registry.backend_name_for(prefix),
            latency_ms=latency_ms,
            chars=len(text),
        )

    result = await synthesize_mp3(
        text,
        voice=voice,
        pitch=pitch,
        speed=speed,
        registry=voice_registry,
    )
    await server.push(session, result.audio)
    await server.close_session(session)
    latency_ms = int((time.perf_counter() - started) * 1000)
    return StreamPublishResult(
        session=session,
        url=server.url_for(session.stream_id),
        backend_name=result.backend_name,
        latency_ms=latency_ms,
        chars=result.chars,
    )


async def _run_feeder(
    server: AudioHttpServer,
    session: AudioStreamSession,
    chunks: AsyncIterator[bytes],
) -> None:
    try:
        async for chunk in chunks:
            if chunk:
                await server.push(session, chunk)
        await server.close_session(session)
    except Exception as exc:
        logger.exception('tts_stream_feed_failed')
        await server.close_session(session, error=str(exc)[:200])
