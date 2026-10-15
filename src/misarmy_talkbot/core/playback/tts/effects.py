"""Shared ffmpeg pitch/speed post-processing for providers without native prosody."""

from __future__ import annotations

import asyncio
import io

from misarmy_talkbot.observability.logger import logger


def ffmpeg_filter_args(
    *, pitch: float, speed: float
) -> tuple[int, float, float]:
    """Map multipliers to asetrate / aresample / atempo (same math as legacy gTTS)."""
    aresample = 24_000
    asetrate = aresample * pitch
    atempo = (1.0 / pitch) * speed
    return aresample, asetrate, atempo


async def apply_ffmpeg_prosody(
    buffer: io.BytesIO, *, pitch: float, speed: float
) -> io.BytesIO:
    """Bake pitch/speed into an MP3 via one ffmpeg pass."""
    aresample, asetrate, atempo = ffmpeg_filter_args(pitch=pitch, speed=speed)
    args = [
        'ffmpeg',
        '-loglevel',
        'error',
        '-f',
        'mp3',
        '-i',
        'pipe:0',
        '-f',
        'mp3',
        '-af',
        f'asetrate={asetrate},aresample={aresample},atempo={atempo}',
        'pipe:1',
    ]
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(input=buffer.getvalue())
    if process.returncode != 0:
        logger.warning(
            'ffmpeg_prosody_failed rc=%s stderr=%s',
            process.returncode,
            stderr.decode('utf-8', errors='replace')[:200],
        )
    output = io.BytesIO(stdout)
    output.seek(0)
    return output
