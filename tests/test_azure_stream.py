"""Azure Speech SDK streaming path (mocked worker)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from misarmy_talkbot.core.playback.tts.exceptions import (
    AzureQuotaExceeded,
    AzureSynthesisError,
)
from misarmy_talkbot.core.playback.tts.providers import azure_backend
from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
    _EOF,
    AzureTtsProvider,
)
from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings

if TYPE_CHECKING:
    import asyncio


@pytest.mark.asyncio
async def test_stream_synthesize_yields_sdk_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_worker(
        _key: str,
        _region: str,
        _ssml: str,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        azure_backend._put_sync(queue, loop, b'hello')
        azure_backend._put_sync(queue, loop, b'world')
        azure_backend._put_sync(queue, loop, _EOF)

    monkeypatch.setattr(azure_backend, '_sdk_stream_to_queue', fake_worker)
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    chunks = [
        c
        async for c in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        )
    ]
    assert chunks == [b'hello', b'world']


@pytest.mark.asyncio
async def test_stream_synthesize_maps_auth_cancel_to_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_worker(
        _key: str,
        _region: str,
        _ssml: str,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        azure_backend._put_sync(
            queue, loop, AzureQuotaExceeded('Azure SDK canceled: nope')
        )
        azure_backend._put_sync(queue, loop, _EOF)

    monkeypatch.setattr(azure_backend, '_sdk_stream_to_queue', fake_worker)
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    with pytest.raises(AzureQuotaExceeded):
        async for _ in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        ):
            pass


@pytest.mark.asyncio
async def test_stream_synthesize_raises_synthesis_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_worker(
        _key: str,
        _region: str,
        _ssml: str,
        queue: asyncio.Queue[Any],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        azure_backend._put_sync(
            queue, loop, AzureSynthesisError('boom', status=0)
        )
        azure_backend._put_sync(queue, loop, _EOF)

    monkeypatch.setattr(azure_backend, '_sdk_stream_to_queue', fake_worker)
    provider = AzureTtsProvider(AzureSpeechSettings(key='k', region='eastus'))
    with pytest.raises(AzureSynthesisError):
        async for _ in provider.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        ):
            pass
