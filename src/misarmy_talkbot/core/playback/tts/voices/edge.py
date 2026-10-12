import io

import edge_tts
import edge_tts.communicate as edge_communicate
from edge_tts.constants import TRUSTED_CLIENT_TOKEN

from misarmy_talkbot.observability.logger import logger

# speech.platform.bing.com now returns 403 on the synthesis socket.
# Edge speaks through api.msedgeservices.com, which still accepts the
# token shipped with edge-tts 7.2.8 (the latest release). Drop this
# once https://github.com/rany2/edge-tts/pull/491 is published.
edge_communicate.WSS_URL = (
    'wss://api.msedgeservices.com/tts/cognitiveservices/websocket/v1'
    f'?Ocp-Apim-Subscription-Key={TRUSTED_CLIENT_TOKEN}'
)


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
