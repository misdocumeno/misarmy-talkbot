import io
import discord
import edge_tts
from ...database.voice import get_voice
from ...logger import logger


async def generate_edge_tts(text: str, member: discord.Member) -> io.BytesIO:
    voice, rate, pitch = await get_user_voice_settings(member)
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buffer = io.BytesIO()

    try:
        async for chunk in comm.stream():
            if chunk['type'] == 'audio':
                buffer.write(chunk['data'])
    except edge_tts.exceptions.NoAudioReceived:
        logger.warning(f'No audio received for text: "{text}"')

    buffer.seek(0)
    return buffer


async def get_user_voice_settings(member: discord.Member) -> tuple[str, str, str]:
    voice_settings = await get_voice(member)
    voice = voice_settings.voice.split('/')[1]
    rate = int((voice_settings.speed - 1.0) * 100)
    pitch = int((voice_settings.pitch - 1.0) * 100)
    return voice, f'{'' if rate < 0 else '+'}{rate}%', f'{'' if pitch < 0 else '+'}{pitch}Hz'
