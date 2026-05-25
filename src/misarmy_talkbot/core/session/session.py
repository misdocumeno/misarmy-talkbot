"""Per-guild bundle of voice pilot, playback engine, and user-facing announcer."""

from __future__ import annotations

import weakref
from contextvars import ContextVar
from typing import TYPE_CHECKING

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.voice.pilot import VoicePilot
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.observability.trace import step

if TYPE_CHECKING:
    import discord

_allow_session_build: ContextVar[bool] = ContextVar(
    '_allow_session_build', default=False
)


def _guild_session_finalize(
    clean: list[bool], object_id: int, guild_id: int
) -> None:
    if not clean[0]:
        logger.warning(
            'LEAK_FINALIZE GuildSession id=%s guild_id=%s', object_id, guild_id
        )


def session_construction_token() -> object:
    return _allow_session_build


class GuildSession:
    """One guild's running resources: TTS queue, voice pilot, and cooldown-aware replies.

    Construction is gated by a context var so only the registry can instantiate sessions;
    that guard exists because accidental ``GuildSession()`` calls would bypass metrics
    and leave supervisors detached from lifecycle ownership.
    """

    def __init__(self, bot: discord.Bot, guild_id: int) -> None:
        if not _allow_session_build.get():
            raise RuntimeError(
                'GuildSession must be constructed via GuildSessionRegistry.get_or_create'
            )
        self.guild_id = guild_id
        self._metrics = MetricsRegistry.instance()
        self.announcer = ErrorReplyAnnouncer(guild_id)
        self.pilot = VoicePilot(bot, guild_id, metrics=self._metrics)
        self.engine = PlaybackEngine(
            guild_id, self.pilot, self.announcer, metrics=self._metrics
        )
        self.pilot.start_supervisor()
        self.engine.start()
        self._finalize_clean = [False]
        weakref.finalize(
            self,
            _guild_session_finalize,
            self._finalize_clean,
            id(self),
            guild_id,
        )
        logger.info(
            'guild_session_created id=%s guild_id=%s', id(self), guild_id
        )

    async def dispose(self) -> None:
        """Stop playback, voice supervisor, and disconnect in order.

        Ordering matters: the engine must finish before the pilot tears down the voice
        client, otherwise FFmpeg or queue workers might touch a dying connection.
        """
        step(self.guild_id, 'session', 'dispose', 'ENTER', session_id=id(self))
        try:
            try:
                await self.engine.shutdown()
            except Exception:
                logger.exception('engine_shutdown guild_id=%s', self.guild_id)
            try:
                await self.pilot.stop_supervisor()
            except Exception:
                logger.exception(
                    'pilot_stop_supervisor guild_id=%s', self.guild_id
                )
            try:
                await self.pilot.disconnect()
            except Exception:
                logger.exception('pilot_disconnect guild_id=%s', self.guild_id)
            logger.info(
                'guild_session_disposed id=%s guild_id=%s',
                id(self),
                self.guild_id,
            )
            step(
                self.guild_id,
                'session',
                'dispose',
                'EXIT',
                session_id=id(self),
            )
        finally:
            self._finalize_clean[0] = True
