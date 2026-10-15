"""Unofficial Edge Read Aloud backend (edge-tts package)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import edge_tts

from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)
from misarmy_talkbot.core.playback.tts.provider import MICROSOFT_PROSODY
from misarmy_talkbot.core.playback.tts.voices.edge import generate_edge_tts

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits


class EdgeTtsProvider:
    backend_name: str = 'edge'
    supports_native_prosody: bool = True
    prosody_limits: ProsodyLimits = MICROSOFT_PROSODY

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

    async def list_voice_ids(self) -> list[str]:
        voices = await edge_tts.list_voices()
        names = [
            voice['ShortName'] for voice in voices if voice.get('ShortName')
        ]
        return sorted(set(names))
