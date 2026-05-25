"""Playback engine queue + health gating (mocked voice client, no FFmpeg/TTS)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import discord
import pytest

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.voice.pilot import VoicePilot
from misarmy_talkbot.observability.metrics import MetricsRegistry
from tests.helpers import (
    FakeBot,
    FakeVoiceClient,
    noop_async,
    ready_audio_message,
)


@pytest.fixture
def engine_setup() -> tuple[
    PlaybackEngine, VoicePilot, FakeVoiceClient, MetricsRegistry
]:
    MetricsRegistry._instance = None
    metrics = MetricsRegistry.instance()
    bot = FakeBot()
    voice_pilot = VoicePilot(bot, 7)  # type: ignore[arg-type]
    voice_client = FakeVoiceClient(connected=True)
    voice_pilot._voice_client = voice_client  # type: ignore[assignment]
    voice_pilot.intended_channel_id = 1
    voice_client.channel.id = 1
    announcer = ErrorReplyAnnouncer(7)
    engine = PlaybackEngine(7, voice_pilot, announcer, metrics=metrics)
    return engine, voice_pilot, voice_client, metrics


def _fake_play_that_finishes(voice_client: FakeVoiceClient) -> object:
    def fake_play(_source: object, *, after: object = None) -> None:
        voice_client.play_count += 1
        voice_client._playing = True

        async def stop_soon() -> None:
            await asyncio.sleep(0.05)
            voice_client._playing = False
            if after is not None:
                after(None)

        asyncio.create_task(stop_soon())

    return fake_play


async def _run_speak_briefly(
    engine: PlaybackEngine,
    _voice_pilot: VoicePilot,
    seconds: float = 0.35,
) -> None:
    task = asyncio.create_task(engine._speak_loop())
    try:
        await asyncio.wait_for(asyncio.sleep(seconds), timeout=seconds + 0.5)
    finally:
        engine._stopped = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_speak_loop_plays_after_gateway_transient_clears(
    engine_setup: tuple[
        PlaybackEngine, VoicePilot, FakeVoiceClient, MetricsRegistry
    ],
) -> None:
    engine, voice_pilot, voice_client, metrics = engine_setup
    voice_pilot.set_gateway_transient(True)
    await engine._queue.put(ready_audio_message())

    voice_client.play = _fake_play_that_finishes(voice_client)  # type: ignore[method-assign]

    async def clear_transient() -> None:
        await asyncio.sleep(0.05)
        voice_pilot.set_gateway_transient(False)

    asyncio.create_task(clear_transient())
    with (
        patch(
            'misarmy_talkbot.core.playback.engine.discord.FFmpegPCMAudio',
            return_value=object(),
        ),
        patch.object(voice_pilot, '_force_fresh_connect', new=noop_async),
    ):
        await _run_speak_briefly(engine, voice_pilot, seconds=0.5)
    assert voice_client.play_count >= 1
    assert metrics._counters.get('messages_played_total', {}).get(7, 0) >= 1


@pytest.mark.asyncio
async def test_play_client_exception_does_not_count_as_played(
    engine_setup: tuple[
        PlaybackEngine, VoicePilot, FakeVoiceClient, MetricsRegistry
    ],
) -> None:
    engine, voice_pilot, voice_client, metrics = engine_setup
    await engine._queue.put(ready_audio_message())

    def failing_play(_source: object, *, _after: object = None) -> None:
        raise discord.errors.ClientException('busy')

    voice_client.play = failing_play  # type: ignore[method-assign]
    with (
        patch(
            'misarmy_talkbot.core.playback.engine.discord.FFmpegPCMAudio',
            return_value=object(),
        ),
        patch.object(voice_pilot, '_force_fresh_connect', new=noop_async),
    ):
        await _run_speak_briefly(engine, voice_pilot, seconds=0.25)
    assert metrics._counters.get('messages_played_total', {}).get(7, 0) == 0


@pytest.mark.asyncio
async def test_speak_loop_recovers_when_voice_down(
    engine_setup: tuple[
        PlaybackEngine, VoicePilot, FakeVoiceClient, MetricsRegistry
    ],
) -> None:
    engine, voice_pilot, voice_client, metrics = engine_setup
    voice_client._connected = False
    await engine._queue.put(ready_audio_message())

    async def fake_recover(*_args: object, **_kwargs: object) -> None:
        voice_client._connected = True

    voice_pilot.recover = fake_recover  # type: ignore[method-assign]

    voice_client.play = _fake_play_that_finishes(voice_client)  # type: ignore[method-assign]

    with (
        patch(
            'misarmy_talkbot.core.playback.engine.discord.FFmpegPCMAudio',
            return_value=object(),
        ),
        patch.object(voice_pilot, '_force_fresh_connect', new=noop_async),
    ):
        await _run_speak_briefly(engine, voice_pilot, seconds=0.6)
    assert voice_client.play_count >= 1
    assert metrics._counters.get('messages_played_total', {}).get(7, 0) >= 1


@pytest.mark.asyncio
async def test_enqueue_failed_tts_announces_and_removes(
    engine_setup: tuple[
        PlaybackEngine, VoicePilot, FakeVoiceClient, MetricsRegistry
    ],
) -> None:
    engine, _voice_pilot, _voice_client, metrics = engine_setup
    audio = ready_audio_message()
    announced: list[tuple[str, str]] = []

    async def capture_announce(
        _message: object, phase: str, summary: str
    ) -> None:
        announced.append((phase, summary))

    async def mark_failed() -> None:
        audio.state = AudioState.FAILED

    engine._announcer.announce = capture_announce  # type: ignore[method-assign]
    audio.process = mark_failed  # type: ignore[method-assign]

    await engine.enqueue(audio)
    assert len(engine._queue) == 0
    assert metrics._counters.get('tts_failures_total', {}).get(7, 0) == 1
    assert announced and announced[0][0] == 'tts'
