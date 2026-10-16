"""Microsoft router: Azure soft-cap → edge-tts fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from misarmy_talkbot.core.playback.tts.exceptions import AzureSynthesisError
from misarmy_talkbot.core.playback.tts.providers.microsoft_router import (
    MicrosoftTtsRouter,
)
from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
        AzureTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.edge_backend import (
        EdgeTtsProvider,
    )


async def _chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.mark.asyncio
async def test_soft_cap_falls_back_to_edge() -> None:
    azure = AsyncMock()
    azure.synthesize = AsyncMock(return_value=b'azure-audio')
    azure.list_voice_ids = AsyncMock(return_value=['es-AR-ElenaNeural'])

    edge = AsyncMock()
    edge.synthesize = AsyncMock(return_value=b'edge-audio')
    edge.list_voice_ids = AsyncMock(return_value=['es-AR-ElenaNeural'])
    edge.backend_name = 'edge'
    edge.supports_native_prosody = True

    usage_store = AsyncMock()
    usage_store.azure_chars_this_utc_month = AsyncMock(return_value=499_990)

    router = MicrosoftTtsRouter(
        azure=azure,
        edge=cast('EdgeTtsProvider', edge),
        settings=AzureSpeechSettings(
            key='fake', region='eastus', monthly_cap=500_000
        ),
        usage_store=usage_store,
    )

    audio = await router.synthesize(
        'hello world!!',  # 13 chars → 500_003 > cap
        voice_id='es-AR-ElenaNeural',
        pitch=1.0,
        speed=1.0,
    )

    assert audio == b'edge-audio'
    assert router.last_backend_name == 'edge'
    azure.synthesize.assert_not_awaited()
    edge.synthesize.assert_awaited_once()


@pytest.mark.asyncio
async def test_under_cap_uses_azure() -> None:
    azure = AsyncMock()
    azure.synthesize = AsyncMock(return_value=b'azure-audio')

    edge = AsyncMock()
    edge.synthesize = AsyncMock(return_value=b'edge-audio')

    usage_store = AsyncMock()
    usage_store.azure_chars_this_utc_month = AsyncMock(return_value=100)

    router = MicrosoftTtsRouter(
        azure=azure,
        edge=cast('EdgeTtsProvider', edge),
        settings=AzureSpeechSettings(
            key='fake', region='eastus', monthly_cap=500_000
        ),
        usage_store=usage_store,
    )

    audio = await router.synthesize(
        'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
    )
    assert audio == b'azure-audio'
    assert router.last_backend_name == 'azure'
    edge.synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_uses_azure_chunks() -> None:
    azure = AsyncMock()
    azure.stream_synthesize = lambda *_a, **_k: _chunks(b'aa', b'bb')
    edge = AsyncMock()
    edge.stream_synthesize = lambda *_a, **_k: _chunks(b'edge')
    usage_store = AsyncMock()
    usage_store.azure_chars_this_utc_month = AsyncMock(return_value=0)

    router = MicrosoftTtsRouter(
        azure=cast('AzureTtsProvider', azure),
        edge=cast('EdgeTtsProvider', edge),
        settings=AzureSpeechSettings(
            key='fake', region='eastus', monthly_cap=500_000
        ),
        usage_store=usage_store,
    )
    out = [
        chunk
        async for chunk in router.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        )
    ]
    assert out == [b'aa', b'bb']
    assert router.last_backend_name == 'azure'


@pytest.mark.asyncio
async def test_stream_falls_back_to_edge_before_first_byte() -> None:
    async def boom(*_a: object, **_k: object) -> AsyncIterator[bytes]:
        raise AzureSynthesisError('fail', status=500)
        yield b''  # pragma: no cover — makes this an async generator type

    azure = AsyncMock()
    azure.stream_synthesize = boom
    edge = AsyncMock()
    edge.stream_synthesize = lambda *_a, **_k: _chunks(b'edge')
    usage_store = AsyncMock()
    usage_store.azure_chars_this_utc_month = AsyncMock(return_value=0)

    router = MicrosoftTtsRouter(
        azure=cast('AzureTtsProvider', azure),
        edge=cast('EdgeTtsProvider', edge),
        settings=AzureSpeechSettings(
            key='fake', region='eastus', monthly_cap=500_000
        ),
        usage_store=usage_store,
    )
    out = [
        chunk
        async for chunk in router.stream_synthesize(
            'hi', voice_id='es-AR-ElenaNeural', pitch=1.0, speed=1.0
        )
    ]
    assert out == [b'edge']
    assert router.last_backend_name == 'edge'
