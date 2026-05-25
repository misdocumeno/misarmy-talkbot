"""One Lavalink ``wavelink.Player`` per guild, plus connect/move/disconnect.

Replaces the old ``VoicePilot``: there is no health gate, no recovery supervisor,
no close-code handling. Wavelink and Lavalink own voice-WS lifecycle and
playback transport; the bot only asks "be in this channel" / "play this track".
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import discord
import wavelink

from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from discord.ext import commands

    from wavelink.types.request import Request as LavalinkRequest


class LavalinkSession:
    """Per-guild owner of a single ``wavelink.Player`` connection.

    Methods are idempotent: ``ensure_connected_to`` is safe to call when already
    in the right channel; ``disconnect`` is safe when no player exists.
    """

    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self._bot = bot
        self.guild_id = guild_id

    def _guild(self) -> discord.Guild | None:
        return self._bot.get_guild(self.guild_id)

    @property
    def player(self) -> wavelink.Player | None:
        guild = self._guild()
        if guild is None:
            return None
        voice_client = guild.voice_client
        if isinstance(voice_client, wavelink.Player):
            return voice_client
        return None

    async def ensure_connected_to(
        self, channel_id: int
    ) -> None | PermissionError:
        """Connect to ``channel_id`` (or move there) using ``wavelink.Player``."""
        guild = self._guild()
        if guild is None:
            return PermissionError('guild missing')
        channel = guild.get_channel(channel_id)
        if not isinstance(
            channel, discord.VoiceChannel | discord.StageChannel
        ):
            return PermissionError('channel missing')
        perms = channel.permissions_for(guild.me)
        if not perms.connect or not perms.speak:
            return PermissionError('missing voice perms')

        existing = self.player
        if existing is not None:
            if existing.channel.id == channel_id:
                return None
            try:
                await existing.move_to(channel)
                return None
            except discord.Forbidden:
                return PermissionError('forbidden move')
            except wavelink.LavalinkException:
                logger.exception(
                    'lavalink_move_failed guild_id=%s channel_id=%s',
                    self.guild_id,
                    channel_id,
                )
                return PermissionError('move failed')

        try:
            await channel.connect(cls=wavelink.Player, self_deaf=True)
        except discord.Forbidden:
            return PermissionError('forbidden connect')
        except wavelink.LavalinkException:
            logger.exception(
                'lavalink_connect_failed guild_id=%s channel_id=%s',
                self.guild_id,
                channel_id,
            )
            return PermissionError('connect failed')
        except Exception:
            logger.exception(
                'lavalink_connect_unexpected guild_id=%s channel_id=%s',
                self.guild_id,
                channel_id,
            )
            return PermissionError('connect failed')
        logger.info(
            'lavalink_connected guild_id=%s channel_id=%s',
            self.guild_id,
            channel_id,
        )
        return None

    async def disconnect(self) -> None:
        """Disconnect this guild's player; no-op when nothing is connected."""
        player = self.player
        if player is None:
            return
        try:
            await player.disconnect(force=True)
        except Exception:
            logger.exception(
                'lavalink_disconnect_failed guild_id=%s', self.guild_id
            )
        else:
            logger.info('lavalink_disconnected guild_id=%s', self.guild_id)

    async def play_track(self, player: wavelink.Player, track: wavelink.Playable) -> None:
        """Start playback with a Lavalink-safe update payload.

        ``wavelink.Player.play()`` always sends default equalizer bands. Our
        Lavalink config disables equalizer (and other filters we do not use),
        so that payload gets HTTP 400. We PATCH only track + transport fields.
        """
        guild = player.guild
        if guild is None:
            raise RuntimeError('player has no guild')

        if not player.connected:
            try:
                await asyncio.wait_for(player._connection_event.wait(), timeout=10.0)
            except TimeoutError as exc:
                raise RuntimeError('lavalink player not connected') from exc

        request = cast(
            'LavalinkRequest',
            {
                'track': {'encoded': track.encoded},
                'volume': player.volume,
                'position': 0,
                'paused': False,
            },
        )
        await player.node._update_player(guild.id, data=request, replace=True)

        player._current = track
        player._original = track
        player._previous = player._current
        player.queue._loaded = track
        player._paused = False
