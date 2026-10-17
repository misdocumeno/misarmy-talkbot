"""Google Cloud router: Cloud first, soft-cap / error → gTTS."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from misarmy_talkbot.core.playback.tts.exceptions import VoiceNotAllowedError
from misarmy_talkbot.core.playback.tts.providers.google_router import (
    GoogleTtsRouter,
    _looks_like_gtts_voice,
)
from misarmy_talkbot.core.playback.tts.settings import GoogleCloudTtsSettings

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.providers.google_backend import (
        GoogleTtsProvider,
    )
    from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
        GoogleCloudTtsProvider,
    )


def _router(
    *,
    cloud: object,
    gtts: object,
    used: int = 0,
    cap: int = 1_000_000,
) -> GoogleTtsRouter:
    usage = AsyncMock()
    usage.google_cloud_chars_this_utc_month = AsyncMock(return_value=used)
    return GoogleTtsRouter(
        cloud=cast('GoogleCloudTtsProvider', cloud),
        gtts=cast('GoogleTtsProvider', gtts),
        settings=GoogleCloudTtsSettings(
            api_key='fake',
            voice_tier='Chirp3',
            cap_standard_wavenet=4_000_000,
            cap_neural2=cap,
            cap_chirp3=cap,
        ),
        usage_store=usage,
    )


def test_voice_id_heuristics() -> None:
    assert _looks_like_gtts_voice('es-Spanish')
    assert _looks_like_gtts_voice('en')
    assert not _looks_like_gtts_voice('es-US-Standard-A')
    assert not _looks_like_gtts_voice('en-US-Chirp3-HD-Aoede')


@pytest.mark.asyncio
async def test_legacy_gtts_voice_skips_cloud() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock()
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'gtts-mp3')
    router = _router(cloud=cloud, gtts=gtts)
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
    router = _router(cloud=cloud, gtts=gtts)
    audio = await router.synthesize(
        'hola', voice_id='es-US-Chirp3-HD-Aoede', pitch=1.0, speed=1.0
    )
    assert audio == b'fallback'
    assert router.last_backend_name == 'google'


@pytest.mark.asyncio
async def test_soft_cap_falls_back_to_gtts() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock(return_value=b'cloud')
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'gtts')
    # 999_995 + 10 chars > 1_000_000
    router = _router(cloud=cloud, gtts=gtts, used=999_995, cap=1_000_000)
    audio = await router.synthesize(
        '0123456789', voice_id='es-US-Chirp3-HD-Aoede', pitch=1.0, speed=1.0
    )
    assert audio == b'gtts'
    cloud.synthesize.assert_not_awaited()
    assert router.last_backend_name == 'google'
    assert router.take_user_notice_msgid() == 'tts_fallback_gtts_soft_cap'
    assert router.take_user_notice_msgid() is None


@pytest.mark.asyncio
async def test_cloud_error_sets_fallback_notice() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock(side_effect=RuntimeError('boom'))
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'fallback')
    router = _router(cloud=cloud, gtts=gtts)
    await router.synthesize(
        'hola', voice_id='es-US-Chirp3-HD-Aoede', pitch=1.0, speed=1.0
    )
    assert router.take_user_notice_msgid() == 'tts_fallback_gtts_error'


@pytest.mark.asyncio
async def test_studio_rejected_when_not_allowed() -> None:
    cloud = AsyncMock()
    cloud.synthesize = AsyncMock(return_value=b'cloud')
    gtts = AsyncMock()
    gtts.synthesize = AsyncMock(return_value=b'gtts')
    router = _router(cloud=cloud, gtts=gtts)
    with pytest.raises(VoiceNotAllowedError):
        await router.synthesize(
            'hola', voice_id='en-US-Studio-O', pitch=1.0, speed=1.0
        )
    cloud.synthesize.assert_not_awaited()
    gtts.synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_voice_ids_cloud_only() -> None:
    cloud = AsyncMock()
    cloud.list_voice_ids = AsyncMock(
        return_value=['es-US-Chirp3-HD-Aoede', 'es-US-Standard-A']
    )
    gtts = AsyncMock()
    gtts.list_voice_ids = AsyncMock(return_value=['es-Spanish', 'en'])
    router = _router(cloud=cloud, gtts=gtts)
    voices = await router.list_voice_ids()
    assert voices == ['es-US-Chirp3-HD-Aoede', 'es-US-Standard-A']
    gtts.list_voice_ids.assert_not_awaited()
