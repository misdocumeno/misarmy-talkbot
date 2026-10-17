"""Google Cloud Text-to-Speech (REST + Chirp 3 HD streaming)."""

from __future__ import annotations

import asyncio
import base64
import io
from typing import TYPE_CHECKING

import aiohttp

from misarmy_talkbot.core.playback.tts.effects import apply_ffmpeg_prosody
from misarmy_talkbot.core.playback.tts.google_billing import (
    voice_allowed_in_catalog,
)
from misarmy_talkbot.core.playback.tts.provider import ProsodyLimits
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from misarmy_talkbot.core.playback.tts.settings import (
        GoogleCloudTtsSettings,
    )

# Official AudioConfig ranges (Standard / WaveNet / Neural2):
# https://cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize
# Chirp 3 HD: no pitch/rate on REST; streaming speaks OGG_OPUS only.
GOOGLE_CLOUD_PROSODY = ProsodyLimits(
    pitch_min=0.25, pitch_max=3.0, speed_min=0.25, speed_max=4.0
)

_SYNTHESIZE_URL = 'https://texttospeech.googleapis.com/v1/text:synthesize'
_VOICES_URL = 'https://texttospeech.googleapis.com/v1/voices'
_EOF = object()


def _pitch_to_semitones(pitch: float) -> float:
    """Map multiplier (1.0 = default) to Google semitone offset."""
    semitones = (pitch - 1.0) * 12.0
    return max(-20.0, min(20.0, semitones))


def is_chirp_voice(voice_id: str) -> bool:
    """True for Chirp / Chirp3 HD voice short names."""
    lower = voice_id.lower()
    return 'chirp3' in lower or 'chirp-hd' in lower or '-chirp' in lower


class GoogleCloudTtsProvider:
    backend_name: str = 'google_cloud'
    supports_native_prosody: bool = True
    # Chirp 3 HD supports StreamingSynthesize; other tiers stay one-shot REST.
    supports_streaming: bool = True
    prosody_limits: ProsodyLimits = GOOGLE_CLOUD_PROSODY

    def __init__(self, settings: GoogleCloudTtsSettings) -> None:
        if not settings.enabled:
            raise ValueError('Google Cloud TTS settings are not enabled')
        self._settings = settings
        self._voices_cache: list[str] | None = None

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> bytes:
        language_code = _language_code_from_voice(voice_id)
        audio_config: dict[str, object] = {'audioEncoding': 'MP3'}
        # Chirp rejects pitch/speakingRate on AudioConfig; apply ffmpeg after.
        chirp = is_chirp_voice(voice_id)
        if not chirp:
            audio_config['speakingRate'] = max(0.25, min(4.0, speed))
            audio_config['pitch'] = _pitch_to_semitones(pitch)
        body = {
            'input': {'text': text},
            'voice': {'languageCode': language_code, 'name': voice_id},
            'audioConfig': audio_config,
        }
        params = {'key': self._settings.api_key}
        async with aiohttp.ClientSession() as session:
            async with session.post(
                _SYNTHESIZE_URL, params=params, json=body
            ) as response:
                payload = await response.json(content_type=None)
                if response.status != 200:
                    detail = str(payload)[:200]
                    raise RuntimeError(
                        f'Google Cloud TTS HTTP {response.status}: {detail}'
                    )
        encoded = payload.get('audioContent')
        if not isinstance(encoded, str) or not encoded:
            raise RuntimeError('Google Cloud TTS returned empty audioContent')
        audio = base64.b64decode(encoded)
        if chirp and (pitch != 1.0 or speed != 1.0):
            buffer = await apply_ffmpeg_prosody(
                io.BytesIO(audio), pitch=pitch, speed=speed
            )
            audio = buffer.getvalue()
        return audio

    async def stream_synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        pitch: float,
        speed: float,
    ) -> AsyncIterator[bytes]:
        """Stream Chirp 3 HD as OGG Opus chunks; otherwise one-shot MP3.

        Google streaming encodings are PCM/ALAW/MULAW/OGG_OPUS only (no MP3).
        Chirp also has no pitch API; non-default prosody uses one-shot + ffmpeg.
        """
        if not is_chirp_voice(voice_id) or pitch != 1.0 or speed != 1.0:
            audio = await self.synthesize(
                text, voice_id=voice_id, pitch=pitch, speed=speed
            )
            if audio:
                yield audio
            return

        queue: asyncio.Queue[bytes | object] = asyncio.Queue(maxsize=64)
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(
            None,
            _chirp_stream_to_queue,
            self._settings.api_key,
            text,
            voice_id,
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
        params = {'key': self._settings.api_key}
        async with aiohttp.ClientSession() as session:
            async with session.get(_VOICES_URL, params=params) as response:
                if response.status != 200:
                    detail = (await response.text())[:200]
                    raise RuntimeError(
                        f'Google Cloud voices HTTP {response.status}: {detail}'
                    )
                payload = await response.json(content_type=None)
        names: list[str] = []
        for entry in payload.get('voices', []):
            if not isinstance(entry, dict):
                continue
            name = entry.get('name')
            if not isinstance(name, str) or not name:
                continue
            # Drop Studio / InstantCustom / Gemini even when tier=all.
            if not voice_allowed_in_catalog(
                name,
                voice_tier=self._settings.voice_tier,
                allow_studio=self._settings.allow_studio,
            ):
                continue
            names.append(name)
        self._voices_cache = sorted(set(names))
        logger.info(
            'google_cloud_voices_cached count=%s tier=%s',
            len(self._voices_cache),
            self._settings.voice_tier,
        )
        return list(self._voices_cache)


def _chirp_stream_to_queue(
    api_key: str,
    text: str,
    voice_id: str,
    queue: asyncio.Queue[bytes | object],
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Blocking Chirp StreamingSynthesize → async queue (OGG Opus chunks)."""
    try:
        from google.api_core.client_options import ClientOptions
        from google.cloud import texttospeech
    except ImportError:
        _put_sync(
            queue,
            loop,
            RuntimeError('google-cloud-texttospeech is not installed'),
        )
        _put_sync(queue, loop, _EOF)
        return

    try:
        client = texttospeech.TextToSpeechClient(
            client_options=ClientOptions(api_key=api_key)
        )
        language_code = _language_code_from_voice(voice_id)
        streaming_config = texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(
                name=voice_id,
                language_code=language_code,
            ),
            streaming_audio_config=texttospeech.StreamingAudioConfig(
                audio_encoding=texttospeech.AudioEncoding.OGG_OPUS,
            ),
        )

        def request_generator() -> Iterator[
            texttospeech.StreamingSynthesizeRequest
        ]:
            yield texttospeech.StreamingSynthesizeRequest(
                streaming_config=streaming_config
            )
            yield texttospeech.StreamingSynthesizeRequest(
                input=texttospeech.StreamingSynthesisInput(text=text)
            )

        for response in client.streaming_synthesize(request_generator()):
            chunk = response.audio_content
            if chunk:
                _put_sync(queue, loop, bytes(chunk))
        _put_sync(queue, loop, _EOF)
    except Exception as exc:
        logger.exception('google_chirp_stream_failed')
        _put_sync(queue, loop, RuntimeError(f'Chirp stream failed: {exc}'))
        _put_sync(queue, loop, _EOF)


def _put_sync(
    queue: asyncio.Queue[bytes | object],
    loop: asyncio.AbstractEventLoop,
    item: bytes | object,
) -> None:
    fut = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
    fut.result(timeout=120.0)


def _language_code_from_voice(voice_id: str) -> str:
    """``es-US-Chirp3-HD-Aoede`` → ``es-US``."""
    parts = voice_id.split('-')
    if len(parts) >= 2:
        return f'{parts[0]}-{parts[1]}'
    return voice_id
