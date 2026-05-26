"""Guild session registry lifecycle (mocked Lavalink, no Discord voice)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.core.voice.lavalink_session import LavalinkSession
from tests.helpers import noop_async


@pytest.mark.asyncio
async def test_get_or_create_returns_same_session() -> None:
    registry = GuildSessionRegistry.instance()
    bot = MagicMock()
    registry.bind_bot(bot)
    with (
        patch.object(PlaybackEngine, 'start', lambda _self: None),
        patch.object(LavalinkSession, 'disconnect', noop_async),
        patch.object(PlaybackEngine, 'shutdown', noop_async),
    ):
        first = await registry.get_or_create(99)
        second = await registry.get_or_create(99)
        assert first is second
        assert len(registry._sessions) == 1


@pytest.mark.asyncio
async def test_dispose_removes_session() -> None:
    registry = GuildSessionRegistry.instance()
    bot = MagicMock()
    registry.bind_bot(bot)
    with (
        patch.object(PlaybackEngine, 'start', lambda _self: None),
        patch.object(LavalinkSession, 'disconnect', noop_async),
        patch.object(PlaybackEngine, 'shutdown', noop_async),
    ):
        await registry.get_or_create(99)
        await registry.dispose(99)
        assert registry.get(99) is None


@pytest.mark.asyncio
async def test_rapid_follow_unfollow_pattern_keeps_at_most_one_session() -> (
    None
):
    """Dispose must finish before a new session is visible (per-guild lock)."""
    registry = GuildSessionRegistry.instance()
    bot = MagicMock()
    registry.bind_bot(bot)
    with (
        patch.object(PlaybackEngine, 'start', lambda _self: None),
        patch.object(LavalinkSession, 'disconnect', noop_async),
        patch.object(PlaybackEngine, 'shutdown', noop_async),
    ):
        session_a = await registry.get_or_create(5)
        await registry.dispose(5)
        session_b = await registry.get_or_create(5)
        assert session_a is not session_b
        assert len(registry._sessions) == 1
