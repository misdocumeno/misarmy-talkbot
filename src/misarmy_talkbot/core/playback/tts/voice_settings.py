"""Map persisted ``UserVoice`` rows to provider-specific TTS parameters (no I/O).

Prefer ``synthesize_mp3`` + the TTS registry for new code. These helpers remain
for tests and any legacy call sites that need Edge rate/pitch strings or gTTS
lang keys without synthesizing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.effects import ffmpeg_filter_args
from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)

if TYPE_CHECKING:
    from misarmy_talkbot.infra.database.voice import UserVoice


def edge_tts_params(settings: UserVoice) -> tuple[str, str, str]:
    """Return Edge voice name, rate, and pitch strings for ``edge_tts.Communicate``."""
    voice = settings.voice.split('/', 1)[1]
    return (
        voice,
        microsoft_rate_string(settings.speed),
        microsoft_pitch_string(settings.pitch),
    )


def google_tts_params(settings: UserVoice) -> tuple[str, int, float, float]:
    """Return gTTS lang key and ffmpeg filter args derived from ``UserVoice``."""
    voice_id = settings.voice.split('/', 1)[1]
    lang = '-'.join(voice_id.split('-')[:-1]) or voice_id
    aresample, asetrate, atempo = ffmpeg_filter_args(
        pitch=settings.pitch, speed=settings.speed
    )
    return lang, aresample, asetrate, atempo
