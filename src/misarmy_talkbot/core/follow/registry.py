from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class FollowEntry:
    """One followed user, with monotonic join ordering for deterministic master promotion."""

    user_id: int
    since_monotonic: float


@dataclass
class GuildFollowSet:
    """Per-guild follow graph: members, optional master, and per-user ignored text channels."""

    guild_id: int
    master_user_id: int | None = None
    members: dict[int, FollowEntry] = field(default_factory=dict)
    ignored_channel_ids: dict[int, frozenset[int]] = field(
        default_factory=dict
    )
    version: int = 0

    def _bump_version(self, _log_suffix: str) -> None:
        self.version += 1


class FollowRegistry:
    """In-process source of truth for who the bot follows in each guild.

    Kept synchronous and Discord-free so voice events, commands, and grace timers can
    all mutate the same structure without awaiting; async work stays in callers that
    already hold a running event loop.
    """

    _instance: FollowRegistry | None = None

    on_empty: Callable[[int], None] | None = None
    on_master_changed: Callable[[int, int | None, int | None], None] | None = (
        None
    )
    on_unfollow: Callable[[int, int], None] | None = None

    def __init__(self) -> None:
        self._guilds: dict[int, GuildFollowSet] = {}

    @classmethod
    def instance(cls) -> FollowRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _set(self, guild_id: int) -> GuildFollowSet:
        if guild_id not in self._guilds:
            self._guilds[guild_id] = GuildFollowSet(guild_id=guild_id)
        return self._guilds[guild_id]

    def follow(self, guild_id: int, user_id: int) -> bool:
        follow_set = self._set(guild_id)
        if user_id in follow_set.members:
            return False
        follow_set.members[user_id] = FollowEntry(
            user_id=user_id, since_monotonic=time.monotonic()
        )
        follow_set._bump_version(f'follow user_id={user_id}')
        return True

    def unfollow(self, guild_id: int, user_id: int) -> bool:
        follow_set = self._guilds.get(guild_id)
        if follow_set is None or user_id not in follow_set.members:
            return False

        was_master = follow_set.master_user_id == user_id
        old_master = follow_set.master_user_id
        del follow_set.members[user_id]
        if user_id in follow_set.ignored_channel_ids:
            del follow_set.ignored_channel_ids[user_id]

        new_master = follow_set.master_user_id
        emptied = len(follow_set.members) == 0

        if emptied:
            follow_set.master_user_id = None
            new_master = None
            follow_set._bump_version(f'unfollow emptied user_id={user_id}')
            del self._guilds[guild_id]
            if was_master and self.on_master_changed is not None:
                self.on_master_changed(guild_id, old_master, None)
            if self.on_unfollow is not None:
                self.on_unfollow(guild_id, user_id)
            if self.on_empty is not None:
                self.on_empty(guild_id)
            return True

        if was_master:
            promoted = min(
                follow_set.members.values(),
                key=lambda entry: entry.since_monotonic,
            )
            follow_set.master_user_id = promoted.user_id
            new_master = promoted.user_id
            follow_set._bump_version(
                f'unfollow promote_master user_id={user_id} new_master={new_master}'
            )
        else:
            follow_set._bump_version(f'unfollow user_id={user_id}')

        if self.on_unfollow is not None:
            self.on_unfollow(guild_id, user_id)
        if was_master and self.on_master_changed is not None:
            self.on_master_changed(guild_id, old_master, new_master)
        return True

    def set_master(self, guild_id: int, user_id: int | None) -> None:
        follow_set = self._set(guild_id)
        previous_master_id = follow_set.master_user_id
        if previous_master_id == user_id:
            return
        follow_set.master_user_id = user_id
        follow_set._bump_version(f'set_master {previous_master_id}->{user_id}')
        if self.on_master_changed is not None:
            self.on_master_changed(guild_id, previous_master_id, user_id)

    def master(self, guild_id: int) -> int | None:
        follow_set = self._guilds.get(guild_id)
        return None if follow_set is None else follow_set.master_user_id

    def members(self, guild_id: int) -> frozenset[int]:
        follow_set = self._guilds.get(guild_id)
        if follow_set is None:
            return frozenset()
        return frozenset(follow_set.members)

    def is_followed(self, guild_id: int, user_id: int) -> bool:
        follow_set = self._guilds.get(guild_id)
        return follow_set is not None and user_id in follow_set.members

    def is_tracked(self, guild_id: int) -> bool:
        return guild_id in self._guilds

    def discard_guild(self, guild_id: int) -> None:
        self._guilds.pop(guild_id, None)

    def toggle_ignore_channel(
        self, guild_id: int, user_id: int, channel_id: int
    ) -> str:
        follow_set = self._guilds.get(guild_id)
        if follow_set is None or user_id not in follow_set.members:
            return 'not_following'
        ignored = set(follow_set.ignored_channel_ids.get(user_id, frozenset()))
        if channel_id in ignored:
            ignored.remove(channel_id)
            action = 'unignored'
        else:
            ignored.add(channel_id)
            action = 'ignored'
        if ignored:
            follow_set.ignored_channel_ids[user_id] = frozenset(ignored)
        else:
            follow_set.ignored_channel_ids.pop(user_id, None)
        follow_set._bump_version(
            f'ignore_toggle user_id={user_id} channel_id={channel_id} {action}'
        )
        return action

    def is_channel_ignored(
        self, guild_id: int, user_id: int, channel_id: int
    ) -> bool:
        follow_set = self._guilds.get(guild_id)
        if follow_set is None:
            return False
        return channel_id in follow_set.ignored_channel_ids.get(
            user_id, frozenset()
        )

    def snapshot(self) -> dict[int, dict[str, Any]]:
        return {
            guild_id: self.snapshot_guild_static(guild_id)
            for guild_id in list(self._guilds)
        }

    @staticmethod
    def snapshot_guild_static(guild_id: int) -> dict[str, Any]:
        follow_registry = FollowRegistry.instance()
        follow_set = follow_registry._guilds.get(guild_id)
        if follow_set is None:
            return {'members': [], 'master': None, 'version': 0}
        return {
            'members': sorted(follow_set.members.keys()),
            'master': follow_set.master_user_id,
            'version': follow_set.version,
        }

    def remove_non_masters(self, guild_id: int) -> list[int]:
        """Drop every followed user except the current master; returns dropped user ids.

        Used when the master moves voice channel so stale followers do not keep TTS
        subscriptions for users the bot is no longer responsible for.
        """
        follow_set = self._guilds.get(guild_id)
        if follow_set is None or follow_set.master_user_id is None:
            return []
        master_id = follow_set.master_user_id
        dropped = [
            user_id for user_id in follow_set.members if user_id != master_id
        ]
        for user_id in dropped:
            del follow_set.members[user_id]
            follow_set.ignored_channel_ids.pop(user_id, None)
        follow_set._bump_version(
            f'master_move_clear_non_masters kept={master_id} dropped={dropped}'
        )
        return dropped
