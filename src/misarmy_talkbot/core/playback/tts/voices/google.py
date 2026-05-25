import asyncio
import io
from concurrent.futures import ProcessPoolExecutor

import gtts

from misarmy_talkbot.observability.logger import logger

executor = ProcessPoolExecutor()


class NoTokensError(Exception):
    pass


async def generate_google_tts(
    text: str,
    *,
    voice: str,
    aresample: int,
    asetrate: float,
    atempo: float,
) -> io.BytesIO:
    assert 0.5 <= atempo <= 6.0
    buffer = await asyncio.get_running_loop().run_in_executor(
        executor, _get_audio, text, voice
    )
    return await apply_effects(buffer, aresample, asetrate, atempo)


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
    """Apply pitch/speed effects in a single ffmpeg subprocess pass.

    Effects are baked into the MP3 here rather than via Lavalink filters because
    each user message can have different settings; per-author filter switching
    on the player would race playback start.
    """
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
            'gtts_ffmpeg_failed rc=%s stderr=%s',
            process.returncode,
            stderr.decode('utf-8', errors='replace')[:200],
        )
    output = io.BytesIO(stdout)
    output.seek(0)
    return output
