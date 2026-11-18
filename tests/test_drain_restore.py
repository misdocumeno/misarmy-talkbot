"""Boot-time drain restore logic: what gets re-enqueued, skipped, or kept.

DB and Discord are mocked; the goal is the reconciliation rules, not storage.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from misarmy_talkbot.app import drain
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.drain import (
    GuildDrainState,
    IgnoredChannels,
    PendingItem,
    encode_snapshot,
)


def _guild(
    guild_id: int, pending: tuple[PendingItem, ...] = ()
) -> GuildDrainState:
    return GuildDrainState(
        guild_id=guild_id,
        voice_channel_id=22,
        master_user_id=33,
        follower_ids=(33,),
        ignored=(IgnoredChannels(user_id=33, channel_ids=()),),
        pending=pending,
    )


def _item(message_id: int) -> PendingItem:
    return PendingItem(
        message_id=message_id,
        channel_id=55,
        author_id=44,
        text=f'msg {message_id}',
    )


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replace DB, voice reconcile, session registry, and message fetch."""
    session = MagicMock()
    session.engine.enqueue = AsyncMock()
    registry = MagicMock()
    registry.get_or_create = AsyncMock(return_value=session)

    fake = {
        'load': AsyncMock(),
        'delete': AsyncMock(),
        'reconcile': AsyncMock(),
        'fetch': AsyncMock(),
        'registry': MagicMock(instance=MagicMock(return_value=registry)),
        'session': session,
        'audio_message': MagicMock(side_effect=lambda msg: ('audio', msg)),
    }
    monkeypatch.setattr(drain, 'load_latest_snapshot', fake['load'])
    monkeypatch.setattr(drain, 'delete_snapshot', fake['delete'])
    monkeypatch.setattr(drain, 'reconcile_master_channel', fake['reconcile'])
    monkeypatch.setattr(drain, '_fetch_message', fake['fetch'])
    monkeypatch.setattr(drain, 'GuildSessionRegistry', fake['registry'])
    monkeypatch.setattr(drain, 'AudioMessage', fake['audio_message'])
    monkeypatch.setattr(drain, '_lavalink_ready', asyncio.Event())
    drain.lavalink_ready_event().set()
    return fake


async def test_restore_enqueues_present_messages_in_order_and_skips_missing(
    fakes: dict[str, MagicMock],
) -> None:
    """Deleted-in-Discord messages are skipped; the rest keep their order."""
    fakes['load'].return_value = (
        1,
        encode_snapshot([_guild(1, (_item(900), _item(901), _item(902)))]),
    )
    msgs = {900: MagicMock(name='m900'), 902: MagicMock(name='m902')}
    fakes['fetch'].side_effect = lambda _bot, item: msgs.get(item.message_id)

    await drain.restore_drain_snapshot(MagicMock())

    enqueued = [
        c.args[0] for c in fakes['session'].engine.enqueue.await_args_list
    ]
    assert enqueued == [('audio', msgs[900]), ('audio', msgs[902])]
    fakes['delete'].assert_awaited_once_with(1)


async def test_restore_skips_guild_that_is_no_longer_followed(
    fakes: dict[str, MagicMock],
) -> None:
    """A guild with no follow state after restore is not rejoined at all."""
    untracked = GuildDrainState(
        guild_id=7,
        voice_channel_id=None,
        master_user_id=None,
        follower_ids=(),
        ignored=(),
        pending=(_item(900),),
    )
    fakes['load'].return_value = (2, encode_snapshot([untracked]))

    await drain.restore_drain_snapshot(MagicMock())

    assert not FollowRegistry.instance().is_tracked(7)
    fakes['registry'].instance.return_value.get_or_create.assert_not_awaited()
    fakes['fetch'].assert_not_awaited()
    fakes['delete'].assert_awaited_once_with(2)


async def test_one_failing_guild_does_not_block_others_or_skip_cleanup(
    fakes: dict[str, MagicMock],
) -> None:
    """Guild 1 raises during reconcile; guild 2 still restores; snapshot is consumed."""
    fakes['load'].return_value = (
        3,
        encode_snapshot([_guild(1, (_item(900),)), _guild(2, (_item(910),))]),
    )
    fakes['reconcile'].side_effect = [RuntimeError('voice gone'), None]
    fakes['fetch'].return_value = MagicMock(name='m910')

    await drain.restore_drain_snapshot(MagicMock())

    assert fakes['reconcile'].await_count == 2
    fakes['session'].engine.enqueue.assert_awaited_once()
    fakes['delete'].assert_awaited_once_with(3)


async def test_invalid_payload_is_kept_for_inspection(
    fakes: dict[str, MagicMock],
) -> None:
    """A payload we cannot decode is never replayed and never deleted."""
    fakes['load'].return_value = (4, '{"version": 999}')

    await drain.restore_drain_snapshot(MagicMock())

    fakes['fetch'].assert_not_awaited()
    fakes['session'].engine.enqueue.assert_not_awaited()
    fakes['delete'].assert_not_awaited()


async def test_no_snapshot_is_a_noop(fakes: dict[str, MagicMock]) -> None:
    fakes['load'].return_value = None

    await drain.restore_drain_snapshot(MagicMock())

    fakes['reconcile'].assert_not_awaited()
    fakes['delete'].assert_not_awaited()
