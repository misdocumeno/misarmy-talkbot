"""One Lavalink ``wavelink.Player`` per guild, plus connect/move/disconnect.

Replaces the old ``VoicePilot``: there is no health gate, no recovery supervisor,
no close-code handling. Wavelink and Lavalink own voice-WS lifecycle and
playback transport; the bot only asks "be in this channel" / "play this track".

Voice health: wavelink only self-heals on close code 4014, and ``Player.connected``
stays True after any other voice-WS close. Lavalink's ``playerUpdate`` carries the
real ``state.connected`` flag, so this module tracks that flag and re-joins the
same channel when the voice transport is down (audio would otherwise be "played"
into a dead connection while every Lavalink counter keeps moving).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, cast

import discord
import wavelink

from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    from discord.ext import commands
    from wavelink.types.request import Request as LavalinkRequest


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# How long a "connected: false" playerUpdate must persist before we rejoin.
VOICE_DOWN_GRACE_SEC = _env_float('VOICE_DOWN_GRACE_SEC', 10.0)
# Delay after a voice-WS close so a real kick/move (voice state update) wins.
VOICE_WS_CLOSE_DELAY_SEC = _env_float('VOICE_WS_CLOSE_DELAY_SEC', 3.0)
# Minimum gap between automatic (event-driven) voice recoveries.
VOICE_RECOVERY_COOLDOWN_SEC = _env_float('VOICE_RECOVERY_COOLDOWN_SEC', 20.0)
# Retry a failed rejoin this many times, this many seconds apart.
VOICE_RETRY_MAX = int(_env_float('VOICE_RETRY_MAX', 5.0))
VOICE_RETRY_DELAY_SEC = _env_float('VOICE_RETRY_DELAY_SEC', 30.0)


class LavalinkSession:
    """Per-guild owner of a single ``wavelink.Player`` connection.

    Methods are idempotent: ``ensure_connected_to`` is safe to call when already
    in the right channel; ``disconnect`` is safe when no player exists.
    """

    def __init__(self, bot: commands.Bot, guild_id: int) -> None:
        self._bot = bot
        self.guild_id = guild_id
        self._requested_moves: set[int] = set()
        # Last Lavalink playerUpdate ``connected`` flag (None = not known yet).
        self._voice_ws_connected: bool | None = None
        # Monotonic time when Lavalink first reported the voice WS as down.
        self._voice_down_since: float | None = None
        self._recover_lock = asyncio.Lock()
        self._last_recovery: float | None = None
        self._recovery_task: asyncio.Task[None] | None = None
        # True while our own recovery disconnects and reconnects. The bot's own
        # voice-state events during this window must not drop follow state.
        self._recovering = False
        # Channel to rejoin when the player is gone (failed reconnect leaves none).
        self._home_channel_id: int | None = None
        self._recover_failures = 0

    @property
    def is_recovering(self) -> bool:
        return self._recovering

    def consume_requested_move(self, channel_id: int) -> bool:
        """True once per ``move_to`` we issued, so other moves can be told apart.

        Discord reports a move by us and a move by a moderator with the same
        voice state update; only our own requests are recorded here.
        """
        if channel_id not in self._requested_moves:
            return False
        self._requested_moves.discard(channel_id)
        return True

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

    # ---- voice transport health ------------------------------------------

    @property
    def voice_ws_connected(self) -> bool | None:
        return self._voice_ws_connected

    def voice_down_for(self) -> float | None:
        """Seconds Lavalink has reported the voice WS down, else None."""
        if self._voice_down_since is None:
            return None
        return max(0.0, time.monotonic() - self._voice_down_since)

    def voice_healthy(self) -> bool:
        """True when a player exists and neither wavelink nor Lavalink says it is down."""
        player = self.player
        if player is None or not player.connected:
            return False
        return self._voice_ws_connected is not False

    def note_voice_state(self, connected: bool, ping: int) -> None:
        """Feed a Lavalink playerUpdate ``connected``/``ping`` into health tracking."""
        metrics = MetricsRegistry.instance()
        self._voice_ws_connected = connected
        metrics.set_gauge(
            'lavalink_voice_connected',
            self.guild_id,
            1.0 if connected else 0.0,
        )
        metrics.set_gauge('lavalink_ping_ms', self.guild_id, float(ping))
        if connected:
            if self._voice_down_since is not None:
                logger.info(
                    'voice_restored guild_id=%s down_for_s=%.1f',
                    self.guild_id,
                    time.monotonic() - self._voice_down_since,
                )
            self._voice_down_since = None
            return
        if self._voice_down_since is None:
            self._voice_down_since = time.monotonic()
            logger.error(
                'voice_down_detected guild_id=%s source=playerUpdate',
                self.guild_id,
            )
        if time.monotonic() - self._voice_down_since >= VOICE_DOWN_GRACE_SEC:
            self.schedule_recovery('playerUpdate_disconnected')

    def schedule_recovery(self, reason: str, *, delay: float = 0.0) -> None:
        """Rejoin the current channel in the background, rate limited by cooldown."""
        if self._recovery_task is not None and not self._recovery_task.done():
            return
        if (
            self._last_recovery is not None
            and time.monotonic() - self._last_recovery
            < VOICE_RECOVERY_COOLDOWN_SEC
        ):
            return
        self._recovery_task = asyncio.create_task(
            self._scheduled_recovery(reason, delay),
            name=f'voice_recover_{self.guild_id}',
        )

    async def _scheduled_recovery(self, reason: str, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await self.recover_voice(reason)

    async def recover_voice(self, reason: str) -> bool:
        """Disconnect and rejoin the channel the player is in.

        Keeps follow state untouched. Returns True when a player is back in voice.
        """
        async with self._recover_lock:
            metrics = MetricsRegistry.instance()
            player = self.player
            if player is not None and player.channel is not None:
                self._home_channel_id = player.channel.id
            channel_id = self._home_channel_id
            if channel_id is None:
                return False
            if not FollowRegistry.instance().is_tracked(self.guild_id):
                # A kick/move/unfollow raced us: do not fight the user's intent.
                logger.info(
                    'voice_recovery_skipped guild_id=%s reason=%s untracked',
                    self.guild_id,
                    reason,
                )
                return False
            self._last_recovery = time.monotonic()
            metrics.inc('voice_recovery_total', self.guild_id)
            logger.warning(
                'voice_recovery_start guild_id=%s channel_id=%s reason=%s',
                self.guild_id,
                channel_id,
                reason,
            )
            self._recovering = True
            try:
                await self.disconnect()
                error = await self.ensure_connected_to(channel_id)
            finally:
                self._recovering = False
            if error is not None:
                metrics.inc('voice_recovery_failed_total', self.guild_id)
                logger.error(
                    'voice_recovery_failed guild_id=%s channel_id=%s err=%s '
                    'attempt=%s',
                    self.guild_id,
                    channel_id,
                    error,
                    self._recover_failures + 1,
                )
                self._schedule_retry(reason)
                return False
            self._recover_failures = 0
            self._voice_ws_connected = None
            self._voice_down_since = None
            logger.info(
                'voice_recovery_done guild_id=%s channel_id=%s reason=%s',
                self.guild_id,
                channel_id,
                reason,
            )
            return True

    def _schedule_retry(self, reason: str) -> None:
        """Keep follow state and retry the home channel with a bounded backoff.

        Gives up after ``VOICE_RETRY_MAX`` attempts; the follow state stays, so
        a later ``/follow`` or a voice move by the master picks the bot back up.
        """
        self._recover_failures += 1
        if self._recover_failures > VOICE_RETRY_MAX:
            logger.error(
                'voice_recovery_gave_up guild_id=%s attempts=%s',
                self.guild_id,
                self._recover_failures,
            )
            return
        self._recovery_task = asyncio.create_task(
            self._scheduled_recovery(f'{reason}_retry', VOICE_RETRY_DELAY_SEC),
            name=f'voice_retry_{self.guild_id}',
        )

    def on_voice_ws_closed(self, code: object, by_remote: bool) -> None:
        """Wavelink reported the voice WS closed. Recover when Discord dropped it."""
        metrics = MetricsRegistry.instance()
        metrics.inc('voice_ws_closed_total', self.guild_id)
        logger.error(
            'voice_ws_closed guild_id=%s code=%s by_remote=%s',
            self.guild_id,
            code,
            by_remote,
        )
        if by_remote:
            self.schedule_recovery('ws_closed', delay=VOICE_WS_CLOSE_DELAY_SEC)

    # ---- connect / move / disconnect -------------------------------------

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
            self._requested_moves.add(channel_id)
            try:
                await existing.move_to(channel)
                return None
            except discord.Forbidden:
                self._requested_moves.discard(channel_id)
                return PermissionError('forbidden move')
            except wavelink.LavalinkException:
                self._requested_moves.discard(channel_id)
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

    async def play_track(
        self, player: wavelink.Player, track: wavelink.Playable
    ) -> None:
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
                await asyncio.wait_for(
                    player._connection_event.wait(), timeout=10.0
                )
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
