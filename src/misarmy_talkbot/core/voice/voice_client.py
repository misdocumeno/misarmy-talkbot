"""Thin ``VoiceClient`` subclass that forwards websocket close codes to the pilot queue.

Why subclass instead of monkey-patching: discord.py owns the voice websocket lifecycle;
we only need a reliable hook for close codes and server updates so recovery policy stays
in ``VoicePilot`` without forking the library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.voice import VoiceClient

from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    import asyncio

    import discord

    pass


class MisarmyVoiceClient(VoiceClient):
    """Bridge library voice events into our pilot queues without copying discord.py internals."""

    def __init__(
        self, client: discord.Bot, channel: discord.abc.Connectable
    ) -> None:
        super().__init__(client, channel)
        self._close_observer: asyncio.Queue[int] | None = None
        self._voice_server_signal: asyncio.Event | None = None
        self._suppress_close_observer = False

    def attach_observer(self, queue: asyncio.Queue[int]) -> None:
        self._close_observer = queue

    def attach_server_signal(self, event: asyncio.Event) -> None:
        self._voice_server_signal = event

    def set_suppress_close_observer(self, suppress: bool) -> None:
        """When true, intentional disconnects do not enqueue synthetic close codes.

        The pilot forces disconnects during recovery; without suppression those would look
        like user-visible voice errors and trigger duplicate recovery paths.
        """
        self._suppress_close_observer = suppress

    def _guild_id(self) -> int | None:
        guild = getattr(self.channel, 'guild', None)
        return guild.id if guild is not None else None

    async def disconnect(self, *, force: bool = False) -> None:
        guild_id = self._guild_id()
        if self._suppress_close_observer:
            logger.debug(
                'voice_disconnect_call guild_id=%s suppress_close_observer=True initiated_by=us',
                guild_id,
            )
            await super().disconnect(force=force)
            return
        code: int | None = None
        try:
            code = getattr(self.ws, 'close_code', None)
        except Exception:
            code = None
        if code is not None:
            logger.warning(
                'voice_disconnect_close_code guild_id=%s code=%s force=%s '
                'initiated_by=voice_ws (non-suppressed disconnect path)',
                guild_id,
                code,
                force,
            )
            if self._close_observer is not None:
                self._close_observer.put_nowait(int(code))
        else:
            logger.warning(
                'voice_disconnect_no_close_code guild_id=%s force=%s '
                'initiated_by=voice_ws (non-suppressed disconnect path)',
                guild_id,
                force,
            )
        await super().disconnect(force=force)

    async def on_voice_server_update(self, data: dict) -> None:  # type: ignore[override]
        if self._voice_server_signal is not None:
            self._voice_server_signal.set()
        await super().on_voice_server_update(data)  # pyright: ignore[reportArgumentType]
