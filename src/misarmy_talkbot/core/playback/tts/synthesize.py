"""Shared synthesize path: provider call + optional ffmpeg prosody."""

from __future__ import annotations

import io
import time
from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.core.playback.tts.registry import TtsRegistry

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import TtsProvider


class SynthesisResult:
    """MP3 bytes plus metering metadata from one synthesis."""

    __slots__ = ('audio', 'backend_name', 'latency_ms', 'chars')

    def __init__(
        self,
        *,
        audio: bytes,
        backend_name: str,
        latency_ms: int,
        chars: int,
    ) -> None:
        self.audio = audio
        self.backend_name = backend_name
        self.latency_ms = latency_ms
        self.chars = chars


async def synthesize_mp3(
    text: str,
    *,
    voice: str,
    pitch: float,
    speed: float,
    registry: TtsRegistry | None = None,
) -> SynthesisResult:
    """Synthesize ``text`` for a full voice id (``edge/...`` / ``google/...``)."""
    voice_registry = registry or TtsRegistry.instance()
    prefix, voice_id = voice_registry.parse_voice(voice)
    provider = voice_registry.get(prefix)

    started = time.perf_counter()
    audio = await provider.synthesize(
        text, voice_id=voice_id, pitch=pitch, speed=speed
    )
    audio = await _maybe_apply_ffmpeg(
        provider, audio, pitch=pitch, speed=speed
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    backend_name = voice_registry.backend_name_for(prefix)
    return SynthesisResult(
        audio=audio,
        backend_name=backend_name,
        latency_ms=latency_ms,
        chars=len(text),
    )


async def _maybe_apply_ffmpeg(
    provider: TtsProvider,
    audio: bytes,
    *,
    pitch: float,
    speed: float,
) -> bytes:
    if provider.supports_native_prosody:
        return audio
    if pitch == 1.0 and speed == 1.0:
        return audio
    buffer = await apply_ffmpeg_prosody(
        io.BytesIO(audio), pitch=pitch, speed=speed
    )
    return buffer.getvalue()
