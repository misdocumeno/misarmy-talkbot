"""Per-guild bundle of Lavalink session, playback engine, and announcer."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.voice.lavalink_session import LavalinkSession
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    from discord.ext import commands

_allow_session_build: ContextVar[bool] = ContextVar(
    '_allow_session_build', default=False
)


def session_construction_token() -> object:
    return _allow_session_build


class GuildSession:
    """One guild's running resources: Lavalink session, TTS queue, and replies.

    Construction is gated by a context var so only the registry can instantiate
    sessions; callers must go through ``GuildSessionRegistry.get_or_create``.
    """

    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        if not _allow_session_build.get():
            raise RuntimeError(
                'GuildSession must be constructed via '
                'GuildSessionRegistry.get_or_create'
            )
        self.guild_id = guild_id
        self._metrics = MetricsRegistry.instance()
        self.announcer = ErrorReplyAnnouncer(guild_id)
        self.lavalink = LavalinkSession(bot, guild_id)
        self.engine = PlaybackEngine(
            guild_id, self.lavalink, self.announcer, metrics=self._metrics
        )
        self.engine.start()
        logger.info(
            'guild_session_created id=%s guild_id=%s', id(self), guild_id
        )

    async def dispose(self) -> None:
        """Stop the engine, then disconnect Lavalink (in that order).

        Engine first so the speak loop is not mid-``player.play`` when the
        underlying Lavalink player disconnects.
        """
        try:
            await self.engine.shutdown()
        except Exception:
            logger.exception('engine_shutdown guild_id=%s', self.guild_id)
        try:
            await self.lavalink.disconnect()
        except Exception:
            logger.exception('lavalink_disconnect guild_id=%s', self.guild_id)
        logger.info(
            'guild_session_disposed id=%s guild_id=%s',
            id(self),
            self.guild_id,
        )
