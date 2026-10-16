"""Chirp voice helpers and one-shot fallback when streaming is unavailable."""

from __future__ import annotations

import pytest

from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
    GoogleCloudTtsProvider,
    is_chirp_voice,
)
from misarmy_talkbot.core.playback.tts.settings import GoogleCloudTtsSettings


def test_is_chirp_voice() -> None:
    assert is_chirp_voice('es-US-Chirp3-HD-Aoede')
    assert is_chirp_voice('en-US-Chirp3-HD-Charon')
    assert not is_chirp_voice('es-US-Standard-A')
    assert not is_chirp_voice('es-Spanish')


@pytest.mark.asyncio
async def test_stream_non_chirp_falls_back_to_synthesize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = GoogleCloudTtsProvider(
        GoogleCloudTtsSettings(api_key='k', voice_tier='Standard')
    )

    async def fake_synth(
        text: str, *, voice_id: str, pitch: float, speed: float
    ) -> bytes:
        _ = text, voice_id, pitch, speed
        return b'mp3-bytes'

    monkeypatch.setattr(provider, 'synthesize', fake_synth)
    out = [
        c
        async for c in provider.stream_synthesize(
            'hi', voice_id='es-US-Standard-A', pitch=1.0, speed=1.0
        )
    ]
    assert out == [b'mp3-bytes']
