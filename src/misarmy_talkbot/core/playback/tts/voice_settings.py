"""Map persisted ``UserVoice`` rows to provider-specific TTS parameters (no I/O)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from misarmy_talkbot.infra.database.voice import UserVoice


def edge_tts_params(settings: UserVoice) -> tuple[str, str, str]:
    """Return Edge voice name, rate, and pitch strings for ``edge_tts.Communicate``."""
    voice = settings.voice.split('/')[1]
    rate = int((settings.speed - 1.0) * 100)
    pitch = int((settings.pitch - 1.0) * 100)
    rate_s = f'{"" if rate < 0 else "+"}{rate}%'
    pitch_s = f'{"" if pitch < 0 else "+"}{pitch}Hz'
    return voice, rate_s, pitch_s


def google_tts_params(settings: UserVoice) -> tuple[str, int, float, float]:
    """Return gTTS lang key and ffmpeg filter args derived from ``UserVoice``."""
    voice = '-'.join(settings.voice.split('/')[1].split('-')[:-1])
    aresample = 24_000
    asetrate = aresample * settings.pitch
    atempo = 1 / settings.pitch * settings.speed
    return voice, aresample, asetrate, atempo
