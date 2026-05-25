"""Lazy construction and teardown of per-guild ``GuildSession`` objects."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from misarmy_talkbot.core.session.session import (
    GuildSession,
    _allow_session_build,
)
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    import discord


class GuildSessionRegistry:
    """Owns the map from guild id to running session (voice pilot plus playback engine).

    Sessions are created only through ``get_or_create`` so construction hooks and metrics
    stay honest; disposal is serialized per guild to avoid double-free during concurrent
    voice and slash-command traffic.
    """

    _instance: GuildSessionRegistry | None = None

    def __init__(self) -> None:
        self._bot: discord.Bot | None = None
        self._sessions: dict[int, GuildSession] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    @classmethod
    def instance(cls) -> GuildSessionRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def bind_bot(self, bot: discord.Bot) -> None:
        self._bot = bot

    def get(self, guild_id: int) -> GuildSession | None:
        return self._sessions.get(guild_id)

    def iter_alive(self) -> list[GuildSession]:
        return list[GuildSession](self._sessions.values())

    async def get_or_create(self, guild_id: int) -> GuildSession:
        if self._bot is None:
            raise RuntimeError('GuildSessionRegistry.bind_bot not called')
        lock = self._locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            guild_session = self._sessions.get(guild_id)
            if guild_session is not None:
                return guild_session
            token = _allow_session_build.set(True)
            try:
                guild_session = GuildSession(self._bot, guild_id)
            finally:
                _allow_session_build.reset(token)
            self._sessions[guild_id] = guild_session
            MetricsRegistry.instance().set_process(
                'sessions_alive', len(self._sessions)
            )
            return guild_session

    async def dispose(self, guild_id: int) -> None:
        lock = self._locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            guild_session = self._sessions.pop(guild_id, None)
        if guild_session is not None:
            await guild_session.dispose()
        MetricsRegistry.instance().set_process(
            'sessions_alive', len(self._sessions)
        )

    async def dispose_all(self) -> None:
        """Tear down every guild session in parallel with a per-guild timeout.

        SIGTERM gives a bounded shutdown window; gathering avoids serial dispose latency
        across many guilds while ``wait_for`` prevents one stuck guild from starving the rest.
        """
        guild_ids: list[int] = list(self._sessions.keys())

        async def dispose_one(guild_id: int) -> None:
            try:
                await asyncio.wait_for(self.dispose(guild_id), timeout=25.0)
            except TimeoutError:
                logger.error('dispose_all_timeout guild_id=%s', guild_id)
            except Exception:
                logger.exception('dispose_all_error guild_id=%s', guild_id)

        await asyncio.gather(
            *(dispose_one(guild_id) for guild_id in guild_ids)
        )
