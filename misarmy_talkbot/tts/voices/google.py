import io
import asyncio
import discord
import gtts
import ffmpy3
from concurrent.futures import ProcessPoolExecutor
from ...database.voice import get_voice

executor = ProcessPoolExecutor()


class NoTokensError(Exception):
    pass


async def generate_google_tts(text: str, member: discord.Member) -> io.BytesIO:
    voice, aresample, asetrate, atempo = await get_user_voice_settings(member)
    assert atempo >= 0.5 and atempo <= 6.0
    # run it async. even tho it's not async, it runs in a new thread
    buffer = await asyncio.get_running_loop().run_in_executor(executor, _get_audio, text, voice)
    # apply pitch and speed effects
    return await apply_effects(buffer, aresample, asetrate, atempo)


def _get_audio(text: str, voice: str) -> io.BytesIO:
    tts = gtts.gTTS(text, lang=voice)
    if len(tts._tokenize(text)) == 0:
        raise NoTokensError
    buffer = io.BytesIO()
    tts.write_to_fp(buffer)
    buffer.seek(0)
    return buffer


async def apply_effects(buffer: io.BytesIO, aresample: int, asetrate: float, atempo: float) -> io.BytesIO:
    ff = ffmpy3.FFmpeg(
        inputs={'pipe:0': ['-f', 'mp3']},
        outputs={'pipe:1': [
            '-f', 'mp3',
            '-af',
            f'{asetrate=},'
            f'{aresample=},'
            f'{atempo=}'
        ]},
    )
    output = io.BytesIO()
    process = await ff.run_async(input_data=buffer.getvalue(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await process.communicate()
    output.write(stdout)
    output.seek(0)
    return output


async def get_user_voice_settings(member: discord.Member) -> tuple[str, int, float, float]:
    voice_settings = await get_voice(member)
    voice = '-'.join(voice_settings.voice.split('/')[1].split('-')[:-1])
    aresample = 24_000
    asetrate = aresample * voice_settings.pitch
    atempo = 1 / voice_settings.pitch * voice_settings.speed
    return voice, aresample, asetrate, atempo
