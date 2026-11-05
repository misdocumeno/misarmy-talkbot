"""Recover actions preserve follow state while resetting runtime."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from misarmy_talkbot.app.recover_ops import recover_reset_guild
from misarmy_talkbot.core.follow.registry import FollowRegistry
from tests.helpers import reset_singletons


@pytest.mark.asyncio
async def test_reset_guild_preserves_follow_and_recreates_session() -> None:
    reset_singletons()
    follow = FollowRegistry.instance()
    follow.follow(7, 100)
    follow.set_master(7, 100)
    assert follow.master(7) == 100

    bot = MagicMock()
    sessions = MagicMock()
    sessions.dispose = AsyncMock()
    sessions.get_or_create = AsyncMock()

    with (
        patch(
            'misarmy_talkbot.app.recover_ops.GuildSessionRegistry.instance',
            return_value=sessions,
        ),
        patch(
            'misarmy_talkbot.app.recover_ops.reconcile_master_channel',
            new_callable=AsyncMock,
        ) as reconcile,
    ):
        colour, msg = await recover_reset_guild(bot, 7)

    sessions.dispose.assert_awaited_once_with(7)
    sessions.get_or_create.assert_awaited_once_with(7)
    reconcile.assert_awaited_once()
    assert follow.master(7) == 100
    assert follow.is_followed(7, 100)
    assert 'preserve' in msg.lower() or 'follow' in msg.lower()
    assert colour.value != 0
