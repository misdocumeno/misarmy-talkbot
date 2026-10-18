"""Unofficial Edge Read Aloud backend (edge-tts package)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import edge_tts

from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)
from misarmy_talkbot.core.playback.tts.provider import MICROSOFT_PROSODY
from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
    azure_voice_sort_key,
)
from misarmy_talkbot.core.playback.tts.voices.edge import (
    generate_edge_tts,
    stream_edge_tts,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits


class EdgeTtsProvider:
    backend_name: str = 'edge'
    supports_native_prosody: bool = True
    supports_streaming: bool = True
    prosody_limits: ProsodyLimits = MICROSOFT_PROSODY

    def __init__(self) -> None:
        self._voices_cache: list[str] | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        buffer = await generate_edge_tts(
            text,
            voice=voice_id,
            rate=microsoft_rate_string(speed),
            pitch=microsoft_pitch_string(pitch),
        )
        return buffer.getvalue()

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        async for chunk in stream_edge_tts(
            text,
            voice=voice_id,
            rate=microsoft_rate_string(speed),
            pitch=microsoft_pitch_string(pitch),
        ):
            yield chunk

    async def list_voice_ids(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        voices = await edge_tts.list_voices()
        names = [
            voice['ShortName'] for voice in voices if voice.get('ShortName')
        ]
        self._voices_cache = sorted(set(names), key=azure_voice_sort_key)
        return list(self._voices_cache)
