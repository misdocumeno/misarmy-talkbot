"""Voice transport health: Lavalink ``connected`` tracking and rejoin recovery."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from misarmy_talkbot.core.voice import lavalink_session as ls
from misarmy_talkbot.core.voice.lavalink_session import LavalinkSession
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.observability.stall import StallWatcher
from tests.helpers import reset_singletons


def _session(guild_id: int = 555) -> LavalinkSession:
    return LavalinkSession(MagicMock(), guild_id)


def _tracked_registry(tracked: bool = True) -> MagicMock:
    registry = MagicMock()
    registry.is_tracked.return_value = tracked
    return registry


def _player_on(channel_id: int) -> MagicMock:
    player = MagicMock()
    player.channel.id = channel_id
    player.connected = True
    return player


@pytest.mark.asyncio
async def test_playerupdate_disconnected_past_grace_schedules_recovery() -> (
    None
):
    reset_singletons()
    session = _session()
    with (
        patch.object(ls, 'VOICE_DOWN_GRACE_SEC', 0.0),
        patch.object(
            session, 'recover_voice', new=AsyncMock(return_value=True)
        ) as recover,
    ):
        session.note_voice_state(connected=False, ping=-1)
        assert session.voice_down_for() is not None
        await asyncio.sleep(0)  # let the scheduled task run
        if session._recovery_task is not None:
            await session._recovery_task
    recover.assert_awaited_once()


@pytest.mark.asyncio
async def test_connected_playerupdate_clears_down_state() -> None:
    reset_singletons()
    session = _session()
    with patch.object(ls, 'VOICE_DOWN_GRACE_SEC', 999.0):
        session.note_voice_state(connected=False, ping=-1)
        assert session.voice_down_for() is not None
        session.note_voice_state(connected=True, ping=40)
    assert session.voice_down_for() is None
    assert session.voice_ws_connected is True
    gauge = MetricsRegistry.instance()._gauges['lavalink_voice_connected']
    assert gauge[555] == 1.0


def test_voice_unhealthy_when_lavalink_says_disconnected() -> None:
    reset_singletons()
    session = _session()
    player = _player_on(10)
    with patch.object(
        LavalinkSession,
        'player',
        new_callable=PropertyMock,
        return_value=player,
    ):
        assert session.voice_healthy() is True
        session.note_voice_state(connected=False, ping=-1)
        assert session.voice_healthy() is False


@pytest.mark.asyncio
async def test_recover_voice_rejoins_same_channel() -> None:
    reset_singletons()
    session = _session()
    player = _player_on(4242)
    with (
        patch.object(
            LavalinkSession,
            'player',
            new_callable=PropertyMock,
            return_value=player,
        ),
        patch(
            'misarmy_talkbot.core.voice.lavalink_session.FollowRegistry.instance',
            return_value=_tracked_registry(True),
        ),
        patch.object(session, 'disconnect', new=AsyncMock()) as disconnect,
        patch.object(
            session, 'ensure_connected_to', new=AsyncMock(return_value=None)
        ) as connect,
    ):
        ok = await session.recover_voice('test')
    assert ok is True
    disconnect.assert_awaited_once()
    connect.assert_awaited_once_with(4242)
    assert (
        MetricsRegistry.instance()._counters['voice_recovery_total'][555] == 1
    )


@pytest.mark.asyncio
async def test_recovery_flag_is_set_only_while_recovering() -> None:
    reset_singletons()
    session = _session()
    player = _player_on(4242)
    seen: list[bool] = []

    async def connect(_channel_id: int) -> None:
        seen.append(session.is_recovering)
        return None

    with (
        patch.object(
            LavalinkSession,
            'player',
            new_callable=PropertyMock,
            return_value=player,
        ),
        patch(
            'misarmy_talkbot.core.voice.lavalink_session.FollowRegistry.instance',
            return_value=_tracked_registry(True),
        ),
        patch.object(session, 'disconnect', new=AsyncMock()),
        patch.object(session, 'ensure_connected_to', new=connect),
    ):
        assert await session.recover_voice('test') is True
    assert seen == [True]
    assert session.is_recovering is False


@pytest.mark.asyncio
async def test_failed_rejoin_keeps_home_channel_and_retries() -> None:
    reset_singletons()
    session = _session()
    player = _player_on(4242)
    with (
        patch.object(
            LavalinkSession,
            'player',
            new_callable=PropertyMock,
            return_value=player,
        ),
        patch(
            'misarmy_talkbot.core.voice.lavalink_session.FollowRegistry.instance',
            return_value=_tracked_registry(True),
        ),
        patch.object(ls, 'VOICE_RETRY_DELAY_SEC', 999.0),
        patch.object(session, 'disconnect', new=AsyncMock()),
        patch.object(
            session,
            'ensure_connected_to',
            new=AsyncMock(return_value=PermissionError('connect failed')),
        ),
    ):
        assert await session.recover_voice('ws_closed') is False
    # The player is gone after a failed connect; the home channel must remain.
    assert session._home_channel_id == 4242
    assert session._recover_failures == 1
    assert session._recovery_task is not None
    session._recovery_task.cancel()


@pytest.mark.asyncio
async def test_retries_stop_after_max_attempts() -> None:
    reset_singletons()
    session = _session()
    session._home_channel_id = 4242
    with patch.object(ls, 'VOICE_RETRY_MAX', 2):
        session._recover_failures = 2
        session._schedule_retry('ws_closed')
    assert session._recover_failures == 3
    assert session._recovery_task is None


@pytest.mark.asyncio
async def test_recover_voice_skips_when_follow_no_longer_tracked() -> None:
    reset_singletons()
    session = _session()
    player = _player_on(4242)
    with (
        patch.object(
            LavalinkSession,
            'player',
            new_callable=PropertyMock,
            return_value=player,
        ),
        patch(
            'misarmy_talkbot.core.voice.lavalink_session.FollowRegistry.instance',
            return_value=_tracked_registry(False),
        ),
        patch.object(session, 'disconnect', new=AsyncMock()) as disconnect,
    ):
        ok = await session.recover_voice('ws_closed')
    assert ok is False
    disconnect.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_recovery_respects_cooldown() -> None:
    reset_singletons()
    session = _session()
    calls: list[str] = []

    async def fake_recover(reason: str) -> bool:
        calls.append(reason)
        session._last_recovery = time.monotonic()  # what the real method does
        return True

    with patch.object(session, 'recover_voice', new=fake_recover):
        session.schedule_recovery('first')
        await session._recovery_task  # type: ignore[misc]
        session.schedule_recovery('second')  # inside cooldown: ignored
        await asyncio.sleep(0)
    assert calls == ['first']


@pytest.mark.asyncio
async def test_stall_watcher_alerts_when_voice_down_too_long() -> None:
    reset_singletons()
    metrics = MetricsRegistry.instance()
    watcher = StallWatcher()
    watcher._voice_down_sec = 0.01
    watcher._interval = 0.05
    watcher._no_progress_sec = 9999

    engine = MagicMock()
    engine.note_progress_for_stall.return_value = None
    engine.queue.head.return_value = None

    session = MagicMock()
    session.guild_id = 77
    session.engine = engine
    session.lavalink.voice_down_for.return_value = 60.0
    registry = MagicMock()
    registry.iter_alive.return_value = [session]

    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.12)
        stop.set()

    with patch(
        'misarmy_talkbot.observability.stall.GuildSessionRegistry.instance',
        return_value=registry,
    ):
        asyncio.create_task(stop_soon())
        await watcher.run(stop)
    assert metrics._counters.get('stall_detected_total', {}).get(77, 0) >= 1
    assert metrics._gauges['lavalink_voice_down_seconds'][77] == 60.0
