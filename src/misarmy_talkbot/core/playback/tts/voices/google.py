"""gTTS byte generation and legacy ffmpeg helper."""

from __future__ import annotations

import asyncio
import io
from concurrent.futures import ProcessPoolExecutor

import gtts

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.core.playback.tts.exceptions import NoTokensError
from misarmy_talkbot.observability.logger import logger

executor = ProcessPoolExecutor()

# Re-export for older imports.
__all__ = [
    'NoTokensError',
    'apply_effects',
    'generate_google_tts',
    'synthesize_google_raw',
]


async def synthesize_google_raw(text: str, *, lang: str) -> bytes:
    """Return raw gTTS MP3 bytes (no pitch/speed post-process)."""
    buffer = await asyncio.get_running_loop().run_in_executor(
        executor, _get_audio, text, lang
    )
    return buffer.getvalue()


async def generate_google_tts(
    text: str,
    *,
    voice: str,
    aresample: int,
    asetrate: float,
    atempo: float,
) -> io.BytesIO:
    """Legacy path: raw gTTS + ffmpeg effects in one call."""
    _ = aresample
    # atempo may be outside a single ffmpeg factor; apply_ffmpeg_prosody chains.
    raw = await synthesize_google_raw(text, lang=voice)

    pitch = asetrate / 24_000
    speed = atempo * pitch
    return await apply_ffmpeg_prosody(
        io.BytesIO(raw), pitch=pitch, speed=speed
    )


def _get_audio(text: str, voice: str) -> io.BytesIO:
    tts = gtts.gTTS(text, lang=voice)
    if len(tts._tokenize(text)) == 0:
        raise NoTokensError
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    return buffer


async def apply_effects(
    buffer: io.BytesIO, aresample: int, asetrate: float, atempo: float
) -> io.BytesIO:
    """Apply pitch/speed effects (compat wrapper around shared effects)."""
    pitch = asetrate / aresample if aresample else 1.0
    speed = atempo * pitch
    logger.debug('gtts_apply_effects pitch=%s speed=%s', pitch, speed)
    return await apply_ffmpeg_prosody(buffer, pitch=pitch, speed=speed)
