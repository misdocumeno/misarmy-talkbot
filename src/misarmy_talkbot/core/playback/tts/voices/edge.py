import io

import edge_tts

from misarmy_talkbot.observability.logger import logger


async def generate_edge_tts(
    text: str,
    *,
    voice: str,
    rate: str,
    pitch: str,
) -> io.BytesIO:
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buffer = io.BytesIO()

    try:
        async for chunk in comm.stream():
            if chunk.get('type') == 'audio':
                data = chunk.get('data')
                if data is not None:
                    buffer.write(data)
    except edge_tts.exceptions.NoAudioReceived:
        logger.warning('No audio received for text: %r', text)

    buffer.seek(0)
    return buffer
