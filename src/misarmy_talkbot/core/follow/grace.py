"""Timed confirmation before unfollowing users who only briefly left voice.

Without a grace window, flaky client reconnects would churn follow state and voice
sessions; this supervisor centralizes cancel/replace logic so voice handlers stay short.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from misarmy_talkbot.core.follow.registry import FollowRegistry


class DisconnectSupervisor:
    """One pending unfollow task per (guild, user) pair, superseded if state changes."""

    _instance: DisconnectSupervisor | None = None

    def __init__(self) -> None:
        self._tasks: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self.on_grace_confirmed_drop: Callable[[int, int], None] | None = None
        self.on_grace_drop_async: (
            Callable[[int, int], Awaitable[None]] | None
        ) = None

    @classmethod
    def instance(cls) -> DisconnectSupervisor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def default_grace_seconds(self) -> float:
        return float(os.getenv('GRACE_DROP_SECONDS', '60'))

    async def schedule_drop(
        self,
        guild_id: int,
        user_id: int,
        grace_seconds: float | None,
        *,
        should_unfollow: Callable[[], Awaitable[bool]],
        follow_registry: FollowRegistry,
        metrics_hook: Callable[[], None] | None = None,
    ) -> None:
        """Sleep, re-check voice membership, then unfollow and notify hooks.

        Re-validation exists because users often reconnect within seconds; we only want
        to drop follow intent when they are still absent after the full grace interval.
        """
        secs = (
            grace_seconds
            if grace_seconds is not None
            else self.default_grace_seconds()
        )
        from misarmy_talkbot.observability.logger import logger

        async def runner() -> None:
            from misarmy_talkbot.observability.trace import step

            try:
                step(
                    guild_id,
                    'grace',
                    'grace_sleep',
                    'ENTER',
                    user_id=user_id,
                    grace_s=secs,
                )
                await asyncio.sleep(secs)
                if not await should_unfollow():
                    logger.debug(
                        'grace_drop_abort_revalidate guild_id=%s user_id=%s',
                        guild_id,
                        user_id,
                    )
                    step(
                        guild_id,
                        'grace',
                        'grace_sleep',
                        'EXIT',
                        user_id=user_id,
                        result='abort_revalidate',
                    )
                    return
                follow_registry.unfollow(guild_id, user_id)
                from misarmy_talkbot.core.session.registry import (
                    GuildSessionRegistry,
                )

                sessions = GuildSessionRegistry.instance()
                if not follow_registry.is_tracked(guild_id):
                    await sessions.dispose(guild_id)
                else:
                    async_callback = (
                        DisconnectSupervisor.instance().on_grace_drop_async
                    )
                    if async_callback is not None:
                        await async_callback(guild_id, user_id)
                if metrics_hook:
                    metrics_hook()
                sync_callback = (
                    DisconnectSupervisor.instance().on_grace_confirmed_drop
                )
                if sync_callback is not None:
                    sync_callback(guild_id, user_id)
                logger.info(
                    'grace_drop_confirmed guild_id=%s user_id=%s',
                    guild_id,
                    user_id,
                )
                step(
                    guild_id,
                    'grace',
                    'grace_sleep',
                    'EXIT',
                    user_id=user_id,
                    result='dropped',
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    'grace_drop_task_error guild_id=%s user_id=%s',
                    guild_id,
                    user_id,
                )
            finally:
                async with self._lock:
                    self._tasks.pop((guild_id, user_id), None)

        async with self._lock:
            previous = self._tasks.pop((guild_id, user_id), None)
        if previous is not None and not previous.done():
            previous.cancel()
            try:
                await previous
            except asyncio.CancelledError:
                pass
            logger.debug(
                'grace_superseded guild_id=%s user_id=%s', guild_id, user_id
            )

        task = asyncio.create_task(
            runner(), name=f'grace_drop_{guild_id}_{user_id}'
        )
        async with self._lock:
            self._tasks[(guild_id, user_id)] = task
        logger.debug(
            'transient_absent guild_id=%s user_id=%s grace_s=%s',
            guild_id,
            user_id,
            secs,
        )

    async def cancel_drop(self, guild_id: int, user_id: int) -> None:
        from misarmy_talkbot.observability.logger import logger

        async with self._lock:
            previous = self._tasks.pop((guild_id, user_id), None)
        if previous is None or previous.done():
            return
        previous.cancel()
        try:
            await previous
        except asyncio.CancelledError:
            pass
        logger.debug(
            'grace_drop_cancelled guild_id=%s user_id=%s', guild_id, user_id
        )

    async def cancel_all_for_guild(self, guild_id: int) -> None:
        keys = [key for key in self._tasks if key[0] == guild_id]
        for _guild_key, user_id in keys:
            await self.cancel_drop(guild_id, user_id)
