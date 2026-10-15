"""Microsoft router: Azure soft-cap → edge-tts fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from misarmy_talkbot.core.playback.tts.providers.microsoft_router import (
    MicrosoftTtsRouter,
)
from misarmy_talkbot.core.playback.tts.settings import AzureSpeechSettings

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.providers.edge_backend import (
        EdgeTtsProvider,
    )


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
