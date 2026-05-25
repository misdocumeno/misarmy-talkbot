"""Tests for follow registry invariants (master promotion, tracking, snapshots).

These are pure data-structure checks: if they fail, voice and slash-command layers will
disagree about who is followed, which is expensive to debug live in Discord.
"""

from misarmy_talkbot.core.follow.registry import FollowRegistry


def test_follow_unfollow_version_and_master_promotion() -> None:
    follow_registry = FollowRegistry.instance()
    assert follow_registry.follow(1, 10) is True
    follow_registry.set_master(1, 10)
    assert follow_registry.master(1) == 10
    assert follow_registry.follow(1, 20) is True
    version_before = follow_registry.snapshot()[1]['version']
    assert follow_registry.unfollow(1, 10) is True
    assert follow_registry.master(1) == 20
    assert follow_registry.snapshot()[1]['version'] > version_before
    assert follow_registry.unfollow(1, 20) is True
    assert follow_registry.snapshot() == {}


def test_is_tracked() -> None:
    follow_registry = FollowRegistry.instance()
    assert follow_registry.is_tracked(99) is False
    follow_registry.follow(99, 1)
    assert follow_registry.is_tracked(99) is True
    follow_registry.unfollow(99, 1)
    assert follow_registry.is_tracked(99) is False


def test_snapshot() -> None:
    follow_registry = FollowRegistry.instance()
    follow_registry.follow(5, 1)
    follow_registry.set_master(5, 1)
    snap = follow_registry.snapshot()
    assert 5 in snap
    assert snap[5]['master'] == 1
    follow_registry.unfollow(5, 1)
