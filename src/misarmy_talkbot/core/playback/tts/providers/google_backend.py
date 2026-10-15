"""gTTS backend (no native prosody; ffmpeg applied by the shared synthesizer)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gtts.lang

from misarmy_talkbot.core.playback.tts.provider import GOOGLE_PROSODY
from misarmy_talkbot.core.playback.tts.voices.google import (
    synthesize_google_raw,
)

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits


class GoogleTtsProvider:
    backend_name: str = 'google'
    supports_native_prosody: bool = False
    prosody_limits: ProsodyLimits = GOOGLE_PROSODY

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        # pitch/speed applied via ffmpeg in ``synthesize_mp3`` when needed.
        _ = pitch
        _ = speed
        lang = _google_lang_from_voice_id(voice_id)
        return await synthesize_google_raw(text, lang=lang)

    async def list_voice_ids(self) -> list[str]:
        return sorted(
            f'{code}-{country}'
            for code, country in gtts.lang.tts_langs().items()
        )


def _google_lang_from_voice_id(voice_id: str) -> str:
    """``es-Spanish`` → ``es`` (drop display country suffix)."""
    parts = voice_id.split('-')
    if len(parts) <= 1:
        return voice_id
    return '-'.join(parts[:-1])
