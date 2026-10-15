import asyncio
import io
import os

import aiohttp
import edge_tts

from misarmy_talkbot.observability.logger import logger

# Microsoft throttles free Edge TTS; 429s are intermittent under chat bursts.
_MAX_ATTEMPTS = max(1, int(os.getenv('EDGE_TTS_MAX_ATTEMPTS', '4')))
_BASE_BACKOFF_S = float(os.getenv('EDGE_TTS_RETRY_BACKOFF_SECONDS', '1.5'))


def _is_rate_limited(exc: BaseException) -> bool:
    status = getattr(exc, 'status', None)
    return status == 429


async def generate_edge_tts(
    text: str,
    *,
    voice: str,
    rate: str,
    pitch: str,
) -> io.BytesIO:
    buffer = io.BytesIO()
    last_error: BaseException | None = None

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        buffer = io.BytesIO()
        try:
            comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            async for chunk in comm.stream():
                if chunk.get('type') == 'audio':
                    data = chunk.get('data')
                    if data is not None:
                        buffer.write(data)
            break
        except edge_tts.exceptions.NoAudioReceived:
            logger.warning('No audio received for text: %r', text)
            break
        except aiohttp.ClientResponseError as exc:
            # Includes WSServerHandshakeError (429 Too Many Requests).
            last_error = exc
            if not _is_rate_limited(exc) or attempt == _MAX_ATTEMPTS:
                raise

        delay = _BASE_BACKOFF_S * (2 ** (attempt - 1))
        logger.warning(
            'edge_tts_rate_limited attempt=%s/%s retry_in=%.1fs text=%r',
            attempt,
            _MAX_ATTEMPTS,
            delay,
            text,
        )
        await asyncio.sleep(delay)
    else:
        assert last_error is not None
        raise last_error

    buffer.seek(0)
    return buffer
