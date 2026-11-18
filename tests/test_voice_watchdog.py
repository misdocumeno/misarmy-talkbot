"""Voice watchdog: rejoin/recover followed guilds without waiting for events."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from misarmy_talkbot.app import voice_watchdog as vw
from misarmy_talkbot.core.follow.registry import FollowRegistry
from tests.helpers import reset_singletons


def _session(
    guild_id: int, player: object | None, healthy: bool, down_for: float | None
) -> MagicMock:
    session = MagicMock()
    session.guild_id = guild_id
    session.lavalink.player = player
    session.lavalink.voice_healthy.return_value = healthy
    session.lavalink.voice_down_for.return_value = down_for
    return session


def _registry_with(sessions: list[MagicMock]) -> MagicMock:
    registry = MagicMock()
    registry.iter_alive.return_value = sessions
    return registry


def _follow(guild_id: int = 555, master: int = 42) -> None:
    follow = FollowRegistry.instance()
    follow.follow(guild_id, master)
    follow.set_master(guild_id, master)


@pytest.mark.asyncio
async def test_missing_player_rejoins_master_channel() -> None:
    reset_singletons()
    _follow()
    session = _session(555, player=None, healthy=False, down_for=None)
    with (
        patch.object(
            vw.GuildSessionRegistry,
            'instance',
            return_value=_registry_with([session]),
        ),
        patch.object(
            vw, 'reconcile_master_channel', new=AsyncMock()
        ) as reconcile,
    ):
        await vw.watchdog_tick(MagicMock())
    reconcile.assert_awaited_once()


@pytest.mark.asyncio
async def test_untracked_guild_is_ignored() -> None:
    reset_singletons()
    session = _session(555, player=None, healthy=False, down_for=None)
    with (
        patch.object(
            vw.GuildSessionRegistry,
            'instance',
            return_value=_registry_with([session]),
        ),
        patch.object(
            vw, 'reconcile_master_channel', new=AsyncMock()
        ) as reconcile,
    ):
        await vw.watchdog_tick(MagicMock())
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_down_past_grace_schedules_recovery() -> None:
    reset_singletons()
    _follow()
    session = _session(555, player=MagicMock(), healthy=False, down_for=60.0)
    with (
        patch.object(
            vw.GuildSessionRegistry,
            'instance',
            return_value=_registry_with([session]),
        ),
        patch.object(
            vw, 'reconcile_master_channel', new=AsyncMock()
        ) as reconcile,
    ):
        await vw.watchdog_tick(MagicMock())
    session.lavalink.schedule_recovery.assert_called_once_with('watchdog')
    reconcile.assert_not_awaited()


@pytest.mark.asyncio
async def test_healthy_voice_is_left_alone() -> None:
    reset_singletons()
    _follow()
    session = _session(555, player=MagicMock(), healthy=True, down_for=None)
    with patch.object(
        vw.GuildSessionRegistry,
        'instance',
        return_value=_registry_with([session]),
    ):
        await vw.watchdog_tick(MagicMock())
    session.lavalink.schedule_recovery.assert_not_called()


@pytest.mark.asyncio
async def test_down_within_grace_is_not_recovered_yet() -> None:
    reset_singletons()
    _follow()
    session = _session(555, player=MagicMock(), healthy=False, down_for=0.5)
    with (
        patch.object(vw, 'VOICE_DOWN_GRACE_SEC', 10.0),
        patch.object(
            vw.GuildSessionRegistry,
            'instance',
            return_value=_registry_with([session]),
        ),
    ):
        await vw.watchdog_tick(MagicMock())
    session.lavalink.schedule_recovery.assert_not_called()
