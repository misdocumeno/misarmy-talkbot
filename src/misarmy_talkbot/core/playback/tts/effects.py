"""Shared ffmpeg pitch/speed post-processing for providers without native prosody."""

from __future__ import annotations

import asyncio
import io

from misarmy_talkbot.observability.logger import logger

# Official ffmpeg ``atempo`` window (one factor per filter instance):
# https://ffmpeg.org/ffmpeg-filters.html#atempo
_ATEMPO_MIN = 0.5
_ATEMPO_MAX = 100.0


def ffmpeg_filter_args(
    *, pitch: float, speed: float
) -> tuple[int, float, float]:
    """Map multipliers to asetrate / aresample / overall atempo ratio."""
    aresample = 24_000
    asetrate = aresample * pitch
    atempo = (1.0 / pitch) * speed
    return aresample, asetrate, atempo


def split_atempo_factors(atempo: float) -> list[float]:
    """Split an overall tempo ratio into ffmpeg-legal ``atempo`` factors.

    ffmpeg: each factor ∈ ``[0.5, 100]``; daisy-chain for products outside
    that range (documented on the atempo filter page). Raises ``ValueError``
    if ``atempo`` is not positive.
    """

    if atempo <= 0.0:
        raise ValueError(f'atempo must be > 0 (got {atempo})')
    factors: list[float] = []
    remaining = atempo
    while remaining > _ATEMPO_MAX:
        factors.append(_ATEMPO_MAX)
        remaining /= _ATEMPO_MAX
    while remaining < _ATEMPO_MIN:
        factors.append(_ATEMPO_MIN)
        remaining /= _ATEMPO_MIN
    factors.append(remaining)
    return factors


async def apply_ffmpeg_prosody(
    buffer: io.BytesIO, *, pitch: float, speed: float
) -> io.BytesIO:
    """Bake pitch/speed into an MP3 via one ffmpeg pass (chained atempo if needed)."""
    aresample, asetrate, atempo = ffmpeg_filter_args(pitch=pitch, speed=speed)
    atempo_chain = ','.join(
        f'atempo={factor}' for factor in split_atempo_factors(atempo)
    )
    audio_filter = f'asetrate={asetrate},aresample={aresample},{atempo_chain}'
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
        audio_filter,
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
