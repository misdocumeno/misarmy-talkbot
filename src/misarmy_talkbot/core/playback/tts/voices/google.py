import asyncio
import io
from concurrent.futures import ProcessPoolExecutor

import ffmpy3
import gtts

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
    ff = ffmpy3.FFmpeg(
        inputs={'pipe:0': ['-f', 'mp3']},
        outputs={
            'pipe:1': [
                '-f',
                'mp3',
                '-af',
                f'{asetrate=},{aresample=},{atempo=}',
            ]
        },
    )
    output = io.BytesIO()
    process = await ff.run_async(
        input_data=buffer.getvalue(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    output.write(stdout)
    output.seek(0)
    return output
