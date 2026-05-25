"""Tests for grace-window disconnect scheduling.

We care because grace tasks mutate follow state and session disposal asynchronously; these
tests lock down the branch where the last follower leaves versus when others remain.
"""

import asyncio

import pytest

import misarmy_talkbot.core.session.registry as session_registry_mod
from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry


@pytest.mark.asyncio
async def test_grace_unfollow_disposes_when_guild_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_registry = FollowRegistry.instance()
    follow_registry.follow(1, 9)
    disposed: list[int] = []

    class FakeSessionRegistry:
        async def dispose(self, _guild_id: int) -> None:
            disposed.append(_guild_id)

    fake = FakeSessionRegistry()
    monkeypatch.setattr(
        session_registry_mod.GuildSessionRegistry, 'instance', lambda: fake
    )
    disconnect_supervisor = DisconnectSupervisor.instance()
    disconnect_supervisor.on_grace_drop_async = None

    async def should_unfollow() -> bool:
        return True

    await disconnect_supervisor.schedule_drop(
        1,
        9,
        0.01,
        should_unfollow=should_unfollow,
        follow_registry=follow_registry,
    )
    await asyncio.sleep(0.08)
    assert disposed == [1]
    assert not follow_registry.is_tracked(1)


@pytest.mark.asyncio
async def test_grace_unfollow_calls_async_when_guild_still_tracked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_registry = FollowRegistry.instance()
    follow_registry.follow(1, 9)
    follow_registry.follow(1, 10)
    follow_registry.set_master(1, 9)
    seen: list[tuple[int, int]] = []

    class FakeSessionRegistry:
        async def dispose(self, _guild_id: int) -> None:
            raise AssertionError(
                'dispose should not run when followers remain'
            )

    monkeypatch.setattr(
        session_registry_mod.GuildSessionRegistry,
        'instance',
        lambda: FakeSessionRegistry(),
    )
    disconnect_supervisor = DisconnectSupervisor.instance()

    async def on_grace_async(guild_id: int, user_id: int) -> None:
        seen.append((guild_id, user_id))

    disconnect_supervisor.on_grace_drop_async = on_grace_async

    async def should_unfollow() -> bool:
        return True

    await disconnect_supervisor.schedule_drop(
        1,
        9,
        0.01,
        should_unfollow=should_unfollow,
        follow_registry=follow_registry,
    )
    await asyncio.sleep(0.08)
    assert seen == [(1, 9)]
    assert follow_registry.is_tracked(1)
    assert follow_registry.is_followed(1, 10)


@pytest.mark.asyncio
async def test_grace_cancel() -> None:
    follow_registry = FollowRegistry.instance()
    follow_registry.follow(1, 9)
    disconnect_supervisor = DisconnectSupervisor.instance()

    async def should_unfollow() -> bool:
        return True

    await disconnect_supervisor.schedule_drop(
        1,
        9,
        0.2,
        should_unfollow=should_unfollow,
        follow_registry=follow_registry,
    )
    await disconnect_supervisor.cancel_drop(1, 9)
    await asyncio.sleep(0.25)
    assert follow_registry.is_followed(1, 9)
