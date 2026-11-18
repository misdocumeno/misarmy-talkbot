"""Drain (SIGUSR1) state, snapshot codec, engine drain behavior and state building."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from misarmy_talkbot.app.drain import _build_states
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.playback.drain import (
    SNAPSHOT_VERSION,
    DrainController,
    GuildDrainState,
    IgnoredChannels,
    PendingItem,
    SnapshotError,
    decode_snapshot,
    encode_snapshot,
)
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from tests.helpers import reset_singletons


def _state() -> GuildDrainState:
    return GuildDrainState(
        guild_id=1,
        voice_channel_id=22,
        master_user_id=33,
        follower_ids=(33, 44),
        ignored=(IgnoredChannels(user_id=44, channel_ids=(55, 66)),),
        pending=(
            PendingItem(
                message_id=900, channel_id=55, author_id=44, text='hola'
            ),
            PendingItem(message_id=901, channel_id=55, author_id=33, text=''),
        ),
    )


# ---- DrainController ------------------------------------------------------


def test_drain_request_is_idempotent() -> None:
    controller = DrainController()
    assert controller.requested is False
    assert controller.request() is True
    assert controller.request() is False
    assert controller.requested is True
    controller.reset()
    assert controller.requested is False


# ---- snapshot codec -------------------------------------------------------


def test_snapshot_round_trip_is_lossless() -> None:
    states = [_state(), GuildDrainState(2, None, None, (), (), ())]
    assert decode_snapshot(encode_snapshot(states)) == states


def test_snapshot_is_declarative_text_only() -> None:
    raw = encode_snapshot([_state()])
    document = json.loads(raw)
    assert document['version'] == SNAPSHOT_VERSION
    serialized = raw.lower()
    # No audio payloads, file references or Python object reprs.
    for forbidden in (
        '.wav',
        '.mp3',
        'audio_bytes',
        'track_url',
        'audiomessage',
        'object at',
    ):
        assert forbidden not in serialized
    guild = document['guilds'][0]
    assert set(guild) == {
        'guild_id',
        'voice_channel_id',
        'master_user_id',
        'follower_ids',
        'ignored',
        'pending',
    }
    assert set(guild['pending'][0]) == {
        'message_id',
        'channel_id',
        'author_id',
        'text',
    }


def test_decode_rejects_unknown_version() -> None:
    raw = json.dumps({'version': 99, 'guilds': []})
    with pytest.raises(SnapshotError):
        decode_snapshot(raw)


def test_decode_rejects_invalid_json_and_wrong_shapes() -> None:
    with pytest.raises(SnapshotError):
        decode_snapshot('{not json')
    with pytest.raises(SnapshotError):
        decode_snapshot('[]')
    bad_guild = {'version': SNAPSHOT_VERSION, 'guilds': [{'guild_id': '1'}]}
    with pytest.raises(SnapshotError):
        decode_snapshot(json.dumps(bad_guild))


def test_decode_rejects_bool_ids() -> None:
    document = {
        'version': SNAPSHOT_VERSION,
        'guilds': [{'guild_id': True, 'voice_channel_id': None}],
    }
    with pytest.raises(SnapshotError):
        decode_snapshot(json.dumps(document))


# ---- engine drain ---------------------------------------------------------


def _engine() -> PlaybackEngine:
    lavalink = MagicMock()
    announcer = MagicMock()
    engine = PlaybackEngine(guild_id=1, lavalink=lavalink, announcer=announcer)
    # Generation (TTS) is out of scope here; keep items as plain queued objects.
    engine._spawn_generation = MagicMock()  # type: ignore[method-assign]
    return engine


def _item(
    message_id: int, state: AudioState = AudioState.PENDING
) -> MagicMock:
    item = MagicMock()
    item.original.id = message_id
    item.state = state
    return item


async def test_idle_drain_stops_speak_loop_and_is_immediately_idle() -> None:
    reset_singletons()
    engine = _engine()
    engine.start()
    await asyncio.sleep(0)  # speak loop parks on an empty head
    engine.begin_drain()
    assert engine.draining is True
    assert await engine.wait_idle(zombie_after_s=0.5) is True


async def test_drain_keeps_enqueued_items_and_never_plays_ready_head() -> None:
    reset_singletons()
    engine = _engine()
    play = AsyncMock()
    engine._play_one = play  # type: ignore[method-assign]

    await engine.enqueue(_item(1, AudioState.READY))
    await engine.enqueue(_item(2, AudioState.PENDING))
    engine.begin_drain()
    engine.start()  # must be a no-op while draining
    await asyncio.sleep(0)

    play.assert_not_called()
    assert [item.original.id for item in engine.pending_items()] == [1, 2]
    assert await engine.wait_idle(zombie_after_s=0.5) is True


async def test_enqueue_during_drain_is_accepted() -> None:
    reset_singletons()
    engine = _engine()
    engine.begin_drain()
    await engine.enqueue(_item(7))
    await engine.enqueue(_item(8))
    assert [item.original.id for item in engine.pending_items()] == [7, 8]


async def test_busy_drain_waits_for_current_item_to_finish() -> None:
    reset_singletons()
    engine = _engine()
    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_play(_audio: MagicMock) -> None:
        started.set()
        await release.wait()

    engine._play_one = fake_play  # type: ignore[method-assign]
    await engine.enqueue(_item(1, AudioState.READY))
    engine.start()
    await asyncio.wait_for(started.wait(), timeout=1.0)

    engine.begin_drain()
    # No progress at all: flagged as a zombie.
    assert await engine.wait_idle(zombie_after_s=0.05, poll_s=0.01) is False
    release.set()
    assert await engine.wait_idle(zombie_after_s=1.0) is True


async def test_slow_message_with_progress_is_not_a_zombie() -> None:
    reset_singletons()
    engine = _engine()
    release = asyncio.Event()

    async def fake_play(_audio: MagicMock) -> None:
        await release.wait()

    engine._play_one = fake_play  # type: ignore[method-assign]
    await engine.enqueue(_item(1, AudioState.READY))
    engine.start()
    await asyncio.sleep(0)
    engine.begin_drain()

    async def playback_heartbeat() -> None:
        # Lavalink position keeps advancing for longer than the zombie window.
        for _ in range(15):
            engine.last_progress_monotonic = time.monotonic()
            await asyncio.sleep(0.02)
        release.set()

    waiter = asyncio.create_task(
        engine.wait_idle(zombie_after_s=0.1, poll_s=0.01)
    )
    await playback_heartbeat()
    assert await asyncio.wait_for(waiter, timeout=1.0) is True


# ---- state building -------------------------------------------------------


async def test_build_states_captures_voice_follow_and_pending() -> None:
    reset_singletons()
    follow = FollowRegistry.instance()
    follow.follow(1, 33)
    follow.follow(1, 44)
    follow.set_master(1, 33)
    follow.toggle_ignore_channel(1, 44, 55)

    message = MagicMock()
    message.id = 900
    message.channel.id = 55
    message.author.id = 44
    item = MagicMock()
    item.original = message
    item.content = 'hola'

    session = MagicMock()
    session.guild_id = 1
    session.lavalink.player.channel.id = 22
    session.engine.pending_items.return_value = [item]

    states = _build_states([session])
    assert len(states) == 1
    state = states[0]
    assert state.voice_channel_id == 22
    assert state.master_user_id == 33
    assert state.follower_ids == (33, 44)
    assert state.ignored == (IgnoredChannels(user_id=44, channel_ids=(55,)),)
    assert state.pending == (
        PendingItem(message_id=900, channel_id=55, author_id=44, text='hola'),
    )
    # Round-trips through the codec without loss.
    assert decode_snapshot(encode_snapshot(states)) == states


async def test_build_states_includes_follow_only_guild_without_session() -> (
    None
):
    reset_singletons()
    FollowRegistry.instance().follow(5, 9)
    states = _build_states([])
    assert len(states) == 1
    assert states[0].guild_id == 5
    assert states[0].voice_channel_id is None
    assert states[0].follower_ids == (9,)
    assert states[0].pending == ()
