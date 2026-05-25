"""Playback engine queue + Lavalink-driven loop (no real Lavalink, no FFmpeg)."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import MagicMock

import discord
import pytest

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.observability.metrics import MetricsRegistry
from tests.helpers import (
    FakeLavalinkSession,
    install_temp_audio_storage,
    ready_audio_message,
)


def _master_channel(player_channel_id: int) -> discord.VoiceChannel:
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = player_channel_id
    return channel


@pytest.fixture
def engine_setup() -> tuple[
    PlaybackEngine, FakeLavalinkSession, MetricsRegistry
]:
    install_temp_audio_storage()
    MetricsRegistry._instance = None
    metrics = MetricsRegistry.instance()
    lavalink = FakeLavalinkSession(guild_id=7)
    announcer = ErrorReplyAnnouncer(7)
    engine = PlaybackEngine(
        7,
        cast('object', lavalink),  # type: ignore[arg-type]
        announcer,
        metrics=metrics,
    )

    def _channel() -> discord.VoiceChannel:
        return _master_channel(lavalink.player.channel.id)

    engine._master_voice_channel = _channel  # type: ignore[method-assign]
    return engine, lavalink, metrics


@pytest.mark.asyncio
async def test_play_one_loads_track_and_completes_on_track_end(
    engine_setup: tuple[PlaybackEngine, FakeLavalinkSession, MetricsRegistry],
) -> None:
    engine, lavalink, metrics = engine_setup
    audio = ready_audio_message()
    await engine._queue.put(audio)

    fake_track = MagicMock(name='track')
    play_started = asyncio.Event()

    async def fake_fetch(_query: str, *, node: object = None) -> list[object]:
        _ = node
        return [fake_track]

    real_play = lavalink.player.play

    async def play_and_signal(
        track: object, *, add_history: bool = False
    ) -> None:
        await real_play(track, add_history=add_history)
        play_started.set()

    lavalink.player.play = play_and_signal  # type: ignore[method-assign]

    import wavelink

    original_fetch = wavelink.Pool.fetch_tracks
    wavelink.Pool.fetch_tracks = fake_fetch  # type: ignore[assignment]
    try:
        loop_task = asyncio.create_task(engine._speak_loop())
        await asyncio.wait_for(play_started.wait(), timeout=1.0)
        engine.on_track_end(MagicMock())
        await asyncio.wait_for(asyncio.sleep(0.05), timeout=0.5)
        engine._stopped = True
        await engine._queue.notify_state_change()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
    finally:
        wavelink.Pool.fetch_tracks = original_fetch  # type: ignore[assignment]

    assert lavalink.player.play_calls == [fake_track]
    assert metrics._counters.get('messages_played_total', {}).get(7, 0) == 1
    assert len(engine._queue) == 0


@pytest.mark.asyncio
async def test_track_exception_clears_current_without_count(
    engine_setup: tuple[PlaybackEngine, FakeLavalinkSession, MetricsRegistry],
) -> None:
    engine, lavalink, metrics = engine_setup
    audio = ready_audio_message()
    await engine._queue.put(audio)

    fake_track = MagicMock(name='track')
    play_started = asyncio.Event()

    async def fake_fetch(_query: str, *, node: object = None) -> list[object]:
        _ = node
        return [fake_track]

    real_play = lavalink.player.play

    async def play_and_signal(
        track: object, *, add_history: bool = False
    ) -> None:
        await real_play(track, add_history=add_history)
        play_started.set()

    lavalink.player.play = play_and_signal  # type: ignore[method-assign]

    import wavelink

    original_fetch = wavelink.Pool.fetch_tracks
    wavelink.Pool.fetch_tracks = fake_fetch  # type: ignore[assignment]
    try:
        loop_task = asyncio.create_task(engine._speak_loop())
        await asyncio.wait_for(play_started.wait(), timeout=1.0)
        payload = MagicMock()
        payload.exception = RuntimeError('boom')
        engine.on_track_exception(payload)
        await asyncio.sleep(0.05)
        engine._stopped = True
        await engine._queue.notify_state_change()
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass
    finally:
        wavelink.Pool.fetch_tracks = original_fetch  # type: ignore[assignment]

    assert metrics._counters.get('messages_played_total', {}).get(7, 0) == 0
    assert (
        metrics._counters.get('lavalink_track_exception_total', {}).get(7, 0)
        == 1
    )
    assert len(engine._queue) == 0


@pytest.mark.asyncio
async def test_enqueue_failed_tts_announces_and_removes(
    engine_setup: tuple[PlaybackEngine, FakeLavalinkSession, MetricsRegistry],
) -> None:
    engine, _lavalink, metrics = engine_setup
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
    pending = list(engine._gen_tasks)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    assert len(engine._queue) == 0
    assert metrics._counters.get('tts_failures_total', {}).get(7, 0) == 1
    assert announced and announced[0][0] == 'tts'


@pytest.mark.asyncio
async def test_stop_member_removes_only_their_messages(
    engine_setup: tuple[PlaybackEngine, FakeLavalinkSession, MetricsRegistry],
) -> None:
    engine, _lavalink, _metrics = engine_setup
    audio_a = ready_audio_message(content='a')
    audio_b = ready_audio_message(content='b')
    cast('discord.Member', audio_a.original.author).id = 100
    cast('discord.Member', audio_b.original.author).id = 200

    await engine._queue.put(audio_a)
    await engine._queue.put(audio_b)

    member = MagicMock(spec=discord.Member)
    member.id = 100
    member.guild_permissions = MagicMock(mute_members=False)
    audio_a.original.author = member
    audio_b.original.author = MagicMock(spec=discord.Member, id=200)

    color, msgid = await engine.stop(member)
    assert msgid == 'shut_up_success'
    assert audio_a not in engine._queue
    assert audio_b in engine._queue
    _ = color
