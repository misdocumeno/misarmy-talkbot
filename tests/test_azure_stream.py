"""Azure TTS SDK push-stream path."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from misarmy_talkbot.core.playback.tts.exceptions import (
    AzureQuotaExceeded,
    AzureSynthesisError,
)
from misarmy_talkbot.core.playback.tts.providers import azure_backend
from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
    AzureTtsProvider,
)
from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings

if TYPE_CHECKING:
    from collections.abc import Callable


def _worker_ok(
    _key: str,
    _region: str,
    _ssml: str,
    queue: asyncio.Queue[bytes | object],
    loop: asyncio.AbstractEventLoop,
) -> None:
    for chunk in (b'\xff\xfbchunk1', b'chunk2'):
        fut = asyncio.run_coroutine_threadsafe(queue.put(chunk), loop)
        fut.result(timeout=5.0)
    fut = asyncio.run_coroutine_threadsafe(queue.put(azure_backend._EOF), loop)
    fut.result(timeout=5.0)


def _worker_exc(
    exc: BaseException,
) -> Callable[..., None]:
    def _run(
        _key: str,
        _region: str,
        _ssml: str,
        queue: asyncio.Queue[bytes | object],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        fut = asyncio.run_coroutine_threadsafe(queue.put(exc), loop)
        fut.result(timeout=5.0)
        fut = asyncio.run_coroutine_threadsafe(
            queue.put(azure_backend._EOF), loop
        )
        fut.result(timeout=5.0)

    return _run


@pytest.mark.asyncio
async def test_stream_synthesize_yields_sdk_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    monkeypatch.setattr(azure_backend, '_sdk_stream_to_queue', _worker_ok)
    chunks = [
        c
        async for c in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        )
    ]
    assert chunks == [b'\xff\xfbchunk1', b'chunk2']


@pytest.mark.asyncio
async def test_stream_synthesize_propagates_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    monkeypatch.setattr(
        azure_backend,
        '_sdk_stream_to_queue',
        _worker_exc(AzureQuotaExceeded('nope')),
    )
    with pytest.raises(AzureQuotaExceeded):
        async for _ in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        ):
            pass


@pytest.mark.asyncio
async def test_stream_synthesize_propagates_synthesis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    monkeypatch.setattr(
        azure_backend,
        '_sdk_stream_to_queue',
        _worker_exc(AzureSynthesisError('boom', status=500)),
    )
    with pytest.raises(AzureSynthesisError):
        async for _ in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        ):
            pass
