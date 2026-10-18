"""Azure Cognitive Services Speech (REST one-shot + SDK push-stream)."""

from __future__ import annotations

import asyncio
import time
import xml.sax.saxutils
from typing import TYPE_CHECKING

import aiohttp

from misarmy_talkbot.core.playback.tts.exceptions import (
    AzureQuotaExceeded,
    AzureSynthesisError,
)
from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)
from misarmy_talkbot.core.playback.tts.provider import MICROSOFT_PROSODY
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
    from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings


_OUTPUT_FORMAT = 'audio-24khz-48kbitrate-mono-mp3'
# Issued tokens are valid ~10 minutes; refresh a bit early.
_TOKEN_TTL_SECONDS = 9 * 60
_EOF = object()


def azure_voice_sort_key(voice_id: str) -> tuple[int, str]:
    """Sort key: standard Neural first, HD / DragonHD last (same F0 char bucket)."""
    lower = voice_id.lower()
    if 'dragonhd' in lower or 'neuralhd' in lower or '-hd-' in lower:
        rank = 2
    elif 'multilingual' in lower:
        rank = 1
    else:
        rank = 0
    return (rank, voice_id)


class AzureTtsProvider:
    backend_name: str = 'azure'
    supports_native_prosody: bool = True
    supports_streaming: bool = True
    prosody_limits: ProsodyLimits = MICROSOFT_PROSODY

    def __init__(self, settings: AzureSpeechSettings) -> None:
        if not settings.enabled:
            raise ValueError('Azure Speech settings are not enabled')
        self._settings = settings
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._voices_cache: list[str] | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        ssml = _build_ssml(
            text,
            voice_id=voice_id,
            pitch=pitch,
            speed=speed,
        )
        token = await self._ensure_token()
        url = (
            f'https://{self._settings.region}.tts.speech.microsoft.com'
            '/cognitiveservices/v1'
        )
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/ssml+xml',
            'X-Microsoft-OutputFormat': _OUTPUT_FORMAT,
            'User-Agent': 'misarmy-talkbot',
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data=ssml.encode('utf-8'), headers=headers
            ) as response:
                body = await response.read()
                if response.status == 200:
                    return body
                if response.status in (401, 403, 429):
                    detail = body.decode('utf-8', errors='replace')[:200]
                    logger.warning(
                        'azure_tts_quotaish status=%s detail=%s',
                        response.status,
                        detail,
                    )
                    raise AzureQuotaExceeded(
                        f'Azure Speech HTTP {response.status}: {detail}'
                    )
                detail = body.decode('utf-8', errors='replace')[:200]
                raise AzureSynthesisError(
                    f'Azure Speech HTTP {response.status}: {detail}',
                    status=response.status,
                )

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        """Yield MP3 chunks as the Speech SDK pushes them (first-byte latency).

        Uses ``PushAudioOutputStream`` so each chunk is a real ``bytes`` copy from
        the SDK ``write(memoryview)`` callback. Avoids ``AudioDataStream.read_data``,
        which requires a ``bytes`` buffer that the C layer mutates (rejects
        ``bytearray``, and is unsafe / empty with immutable ``bytes``).
        """
        ssml = _build_ssml(text, voice_id=voice_id, pitch=pitch, speed=speed)
        queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(
            None,
            _sdk_stream_to_queue,
            self._settings.key,
            self._settings.region,
            ssml,
            queue,
            loop,
        )
        try:
            while True:
                item = await queue.get()
                if item is _EOF:
                    break
                if isinstance(item, BaseException):
                    raise item
                assert isinstance(item, bytes)
                if item:
                    yield item
        finally:
            await worker

    async def list_voice_ids(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        token = await self._ensure_token()
        url = (
            f'https://{self._settings.region}.tts.speech.microsoft.com'
            '/cognitiveservices/voices/list'
        )
        headers = {'Authorization': f'Bearer {token}'}
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, headers=headers) as response,
        ):
            if response.status != 200:
                detail = (await response.text())[:200]
                raise AzureSynthesisError(
                    f'Azure voices list HTTP {response.status}: {detail}',
                    status=response.status,
                )
            payload = await response.json(content_type=None)
        names: list[str] = []
        if isinstance(payload, list):
            for entry in payload:
                if not isinstance(entry, dict):
                    continue
                short_name = entry.get('ShortName')
                if isinstance(short_name, str) and short_name:
                    names.append(short_name)
        self._voices_cache = sorted(set(names), key=azure_voice_sort_key)
        return list(self._voices_cache)

    async def _ensure_token(self) -> str:
        now = time.monotonic()
        if self._token is not None and now < self._token_expires_at:
            return self._token
        url = (
            f'https://{self._settings.region}.api.cognitive.microsoft.com'
            '/sts/v1.0/issueToken'
        )
        headers = {
            'Ocp-Apim-Subscription-Key': self._settings.key,
            'Content-Length': '0',
        }
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, headers=headers) as response,
        ):
            if response.status != 200:
                detail = (await response.text())[:200]
                raise AzureSynthesisError(
                    f'Azure token HTTP {response.status}: {detail}',
                    status=response.status,
                )
            token = await response.text()
        self._token = token
        self._token_expires_at = now + _TOKEN_TTL_SECONDS
        return token


def _sdk_stream_to_queue(
    key: str,
    region: str,
    ssml: str,
    queue: asyncio.Queue[bytes | object],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Blocking Speech SDK push-stream; copies chunks onto ``queue``."""
    try:
        import azure.cognitiveservices.speech as speechsdk
    except ImportError:
        _put_sync(
            queue,
            loop,
            AzureSynthesisError(
                'azure-cognitiveservices-speech is not installed',
                status=0,
            ),
        )
        _put_sync(queue, loop, _EOF)
        return

    try:
        speech_config = speechsdk.SpeechConfig(subscription=key, region=region)
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
        )

        class _PushCallback(speechsdk.audio.PushAudioOutputStreamCallback):
            def __init__(self) -> None:
                super().__init__()
                self.total = 0
                self.first_at: float | None = None

            def write(self, audio_buffer: memoryview) -> int:
                data = bytes(audio_buffer)
                n = audio_buffer.nbytes
                if data:
                    if self.first_at is None:
                        self.first_at = time.perf_counter()
                    self.total += len(data)
                    _put_sync(queue, loop, data)
                return n

            def close(self) -> None:
                return

        started = time.perf_counter()
        callback = _PushCallback()
        push_stream = speechsdk.audio.PushAudioOutputStream(callback)
        audio_config = speechsdk.audio.AudioOutputConfig(stream=push_stream)
        # Keep synthesizer + stream alive for the full speak call.
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config, audio_config=audio_config
        )
        result = synthesizer.speak_ssml_async(ssml).get()
        if result is None:
            raise AzureSynthesisError(
                'Azure SDK returned empty synthesis result', status=0
            )
        if result.reason == speechsdk.ResultReason.Canceled:
            details = result.cancellation_details
            code = getattr(details, 'error_code', None)
            detail = str(getattr(details, 'error_details', details) or '')[
                :200
            ]
            logger.warning(
                'azure_sdk_canceled code=%s detail=%s', code, detail
            )
            if code in (
                speechsdk.CancellationErrorCode.AuthenticationFailure,
                speechsdk.CancellationErrorCode.TooManyRequests,
                speechsdk.CancellationErrorCode.Forbidden,
            ):
                raise AzureQuotaExceeded(f'Azure SDK canceled: {detail}')
            raise AzureSynthesisError(
                f'Azure SDK canceled: {detail}', status=0
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        first_ms = (
            int((callback.first_at - started) * 1000)
            if callback.first_at is not None
            else None
        )
        logger.info(
            'azure_sdk_push_done bytes=%s first_byte_ms=%s finish_ms=%s',
            callback.total,
            first_ms,
            elapsed_ms,
        )
        if callback.total <= 0:
            raise AzureSynthesisError(
                'Azure SDK push stream produced no audio', status=0
            )
        _put_sync(queue, loop, _EOF)
    except (AzureQuotaExceeded, AzureSynthesisError) as exc:
        _put_sync(queue, loop, exc)
        _put_sync(queue, loop, _EOF)
    except Exception as exc:
        logger.exception('azure_sdk_stream_failed')
        _put_sync(
            queue,
            loop,
            AzureSynthesisError(f'Azure SDK stream failed: {exc}', status=0),
        )
        _put_sync(queue, loop, _EOF)


def _put_sync(
    queue: asyncio.Queue[bytes | object],
    loop: asyncio.AbstractEventLoop,
    item: bytes | object,
) -> None:
    fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
    fut.result(timeout=120.0)


def _build_ssml(
    text: str, *, voice_id: str, pitch: float, speed: float
) -> str:
    # xml:lang is required; ShortName locale prefix is good enough for neural.
    locale = '-'.join(voice_id.split('-')[:2]) if '-' in voice_id else 'en-US'
    escaped = xml.sax.saxutils.escape(text)
    rate = microsoft_rate_string(speed)
    pitch_str = microsoft_pitch_string(pitch)
    return (
        "<speak version='1.0' xml:lang="
        f"'{locale}' xmlns='http://www.w3.org/2001/10/synthesis' "
        "xmlns:mstts='https://www.w3.org/2001/mstts'>"
        f"<voice name='{xml.sax.saxutils.escape(voice_id)}'>"
        f"<prosody rate='{rate}' pitch='{pitch_str}'>{escaped}</prosody>"
        '</voice></speak>'
    )
