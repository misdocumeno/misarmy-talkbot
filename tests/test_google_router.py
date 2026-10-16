"""Google Cloud router: Cloud first, gTTS fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from misarmy_talkbot.core.playback.tts.providers.google_router import (
    GoogleTtsRouter,
    _looks_like_gtts_voice,
)

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.providers.google_backend import (
        GoogleTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
        GoogleCloudTtsProvider,
    )


def test_voice_id_heuristics() -> None:
    assert _looks_like_gtts_voice('es-Spanish')
    assert _looks_like_gtts_voice('en')
    assert not _looks_like_gtts_voice('es-US-Standard-A')
    assert not _looks_like_gtts_voice('en-US-Neural2-C')


@pytest.mark.asyncio
async def test_legacy_gtts_voice_skips_cloud() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock()
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'gtts-mp3')
    router = GoogleTtsRouter(
        cloud=cast('GoogleCloudTtsProvider', cloud),
        gtts=cast('GoogleTtsProvider', gtts),
    )
    audio = await router.synthesize(
        'hola', voice_id='es-Spanish', pitch=1.0, speed=1.0
    )
    assert audio == b'gtts-mp3'
    cloud.synthesize.assert_not_awaited()
    assert router.last_backend_name == 'google'


@pytest.mark.asyncio
async def test_cloud_failure_falls_back_to_gtts() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock(side_effect=RuntimeError('boom'))
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'fallback')
    router = GoogleTtsRouter(
        cloud=cast('GoogleCloudTtsProvider', cloud),
        gtts=cast('GoogleTtsProvider', gtts),
    )
    audio = await router.synthesize(
        'hola', voice_id='es-US-Standard-A', pitch=1.0, speed=1.0
    )
    assert audio == b'fallback'
    assert router.last_backend_name == 'google'
