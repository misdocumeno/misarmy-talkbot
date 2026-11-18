"""Periodic voice watchdog: rejoin the master's channel without waiting for traffic.

Event-driven recovery (``playerUpdate`` / voice-WS close) covers most drops, but two
cases are silent: the ``wavelink.Player`` disappears entirely (restart, forced
disconnect that never fired an event), or Lavalink keeps reporting the voice WS as
down with no further event to act on. This loop checks every followed guild on a
fixed interval and repairs it. Playback itself is untouched.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.core.voice.lavalink_session import VOICE_DOWN_GRACE_SEC
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    from discord.ext import commands

_stop: asyncio.Event | None = None


def voice_watchdog_stop_event() -> asyncio.Event:
    global _stop
    if _stop is None:
        _stop = asyncio.Event()
    return _stop


def _interval() -> float:
    raw = os.getenv('VOICE_WATCHDOG_INTERVAL_SEC', '15')
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 15.0


async def watchdog_tick(bot: commands.Bot) -> None:
    """One pass over live sessions: repair guilds we are supposed to be in voice for."""
    follow_registry = FollowRegistry.instance()
    metrics = MetricsRegistry.instance()
    for session in GuildSessionRegistry.instance().iter_alive():
        guild_id = session.guild_id
        if not follow_registry.is_tracked(guild_id):
            continue
        if follow_registry.master(guild_id) is None:
            continue
        lavalink = session.lavalink
        if lavalink.player is None:
            metrics.inc('voice_watchdog_rejoin_total', guild_id)
            logger.warning('voice_watchdog_rejoin guild_id=%s', guild_id)
            await reconcile_master_channel(bot, guild_id)
            continue
        down_for = lavalink.voice_down_for()
        if (
            not lavalink.voice_healthy()
            and down_for is not None
            and down_for >= VOICE_DOWN_GRACE_SEC
        ):
            metrics.inc('voice_watchdog_recover_total', guild_id)
            logger.warning(
                'voice_watchdog_recover guild_id=%s down_for_s=%.1f',
                guild_id,
                down_for,
            )
            lavalink.schedule_recovery('watchdog')


async def run_voice_watchdog(
    bot: commands.Bot, stop_event: asyncio.Event
) -> None:
    interval = _interval()
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            await watchdog_tick(bot)
        except Exception:
            logger.exception('voice_watchdog_tick_failed')
