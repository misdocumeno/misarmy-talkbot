"""Playback completion wait (no real Discord voice)."""

from __future__ import annotations

import asyncio

import pytest

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.voice.pilot import VoicePilot
from misarmy_talkbot.observability.metrics import MetricsRegistry
from tests.helpers import FakeBot, FakeVoiceClient


@pytest.mark.asyncio
async def test_play_source_ok_when_after_missing_but_voice_goes_idle() -> None:
    MetricsRegistry._instance = None
    bot = FakeBot()
    voice_pilot = VoicePilot(bot, 7)  # type: ignore[arg-type]
    voice_client = FakeVoiceClient(connected=True)
    voice_pilot._voice_client = voice_client  # type: ignore[assignment]
    engine = PlaybackEngine(7, voice_pilot, ErrorReplyAnnouncer(7))

    def play_no_after(
        _source: object,
        *,
        after: object | None = None,
    ) -> None:
        _ = after
        voice_client.play_count += 1
        voice_client._playing = True

        async def stop_soon() -> None:
            await asyncio.sleep(0.08)
            voice_client._playing = False

        asyncio.create_task(stop_soon())

    voice_client.play = play_no_after  # type: ignore[method-assign]

    result = await engine._play_source(
        voice_client, object(), audio_bytes=6912
    )
    assert result == 'ok'
    assert voice_client.play_count == 1


@pytest.mark.asyncio
async def test_play_source_ok_when_is_playing_stuck_past_budget() -> None:
    MetricsRegistry._instance = None
    bot = FakeBot()
    voice_pilot = VoicePilot(bot, 7)  # type: ignore[arg-type]
    voice_client = FakeVoiceClient(connected=True)
    voice_pilot._voice_client = voice_client  # type: ignore[assignment]
    engine = PlaybackEngine(7, voice_pilot, ErrorReplyAnnouncer(7))

    def play_stuck(
        _source: object,
        *,
        after: object | None = None,
    ) -> None:
        _ = after
        voice_client.play_count += 1
        voice_client._playing = True

    voice_client.play = play_stuck  # type: ignore[method-assign]

    result = await engine._play_source(
        voice_client, object(), audio_bytes=6912, text_chars=4
    )
    assert result == 'ok'
    assert voice_client.play_count == 1


def test_play_wait_budget_uses_compressed_size_not_pcm() -> None:
    engine = PlaybackEngine(
        7, VoicePilot(FakeBot(), 7), ErrorReplyAnnouncer(7)
    )  # type: ignore[arg-type]
    short_pcm_budget = engine._play_wait_budget_seconds(6912)
    long_line_budget = engine._play_wait_budget_seconds(16992, text_chars=48)
    assert long_line_budget > short_pcm_budget
    assert long_line_budget >= 5.0
