"""Application entry: construct the Discord bot, connect Lavalink, run it.

This layer exists so ``core`` and ``infra`` stay free of global wiring; the bot
instance is the composition root where registries meet Discord callbacks and
where SIGTERM can drain sessions before the process exits.
"""

from __future__ import annotations

import asyncio
import os
import signal
from typing import TYPE_CHECKING

import discord
import wavelink
from discord.ext import commands

if TYPE_CHECKING:
    import types

from misarmy_talkbot.app.commands import register_commands
from misarmy_talkbot.app.events import (
    audio_janitor_stop_event,
    metrics_snapshot_stop_event,
    register_events,
)
from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.logger import logger


class TalkBot(commands.Bot):
    """``commands.Bot`` subclass that connects Lavalink during ``setup_hook``.

    ``setup_hook`` is the discord.py-recommended place for one-time async setup
    that must complete before the gateway dispatches events. Pool connection
    happens here so the first ``on_wavelink_node_ready`` arrives soon after
    READY.
    """

    async def setup_hook(self) -> None:
        host = os.getenv('LAVALINK_HOST', 'lavalink')
        port = os.getenv('LAVALINK_PORT', '2333')
        password = os.getenv('LAVALINK_PASSWORD', 'youshallnotpass')
        node = wavelink.Node(
            uri=f'http://{host}:{port}',
            password=password,
            identifier='main',
        )
        try:
            await wavelink.Pool.connect(nodes=[node], client=self)
            logger.info(
                'lavalink_pool_connect_initiated host=%s port=%s', host, port
            )
        except Exception:
            logger.exception(
                'lavalink_pool_connect_failed host=%s port=%s', host, port
            )


def _wire_singletons(bot: commands.Bot) -> None:
    """Connect follow and session singletons to Discord-facing callbacks.

    Callbacks are registered here (not inside domain classes) so ``core`` does
    not import ``app`` and tests can reset singletons without constructing a
    full bot.
    """
    sessions = GuildSessionRegistry.instance()
    sessions.bind_bot(bot)
    follow_registry = FollowRegistry.instance()
    follow_registry.on_empty = None

    def on_unfollow(guild_id: int, user_id: int) -> None:
        guild_session = sessions.get(guild_id)
        if guild_session is not None:
            guild_session.announcer.reset_cooldown(user_id)

    follow_registry.on_unfollow = on_unfollow

    disconnect_supervisor = DisconnectSupervisor.instance()

    def on_grace_confirmed_drop(guild_id: int, user_id: int) -> None:
        guild_session = sessions.get(guild_id)
        if guild_session is not None:
            guild_session.announcer.reset_cooldown(user_id)

    disconnect_supervisor.on_grace_confirmed_drop = on_grace_confirmed_drop

    async def on_grace_drop_async(guild_id: int, _user_id: int) -> None:
        guild_session = sessions.get(guild_id)
        if guild_session is None:
            return
        await reconcile_master_channel(bot, guild_id)

    disconnect_supervisor.on_grace_drop_async = on_grace_drop_async


def build_bot() -> commands.Bot:
    """Create a configured ``commands.Bot`` with commands and events attached."""
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.voice_states = True
    bot = TalkBot(command_prefix='!', intents=intents)
    _wire_singletons(bot)
    register_commands(bot)
    register_events(bot)
    return bot


def run_bot() -> None:
    """Start the bot process (blocking) and register cooperative shutdown on SIGTERM."""
    bot = build_bot()

    def signal_handler(_signum: int, _frame: types.FrameType | None) -> None:
        async def _close() -> None:
            metrics_snapshot_stop_event().set()
            audio_janitor_stop_event().set()
            try:
                await asyncio.wait_for(
                    GuildSessionRegistry.instance().dispose_all(),
                    timeout=30.0,
                )
            except Exception:
                logger.exception('shutdown_dispose')
            try:
                await wavelink.Pool.close()
            except Exception:
                logger.exception('shutdown_lavalink_close')
            await bot.close()

        bot.loop.create_task(_close())

    signal.signal(signal.SIGTERM, signal_handler)
    logging = __import__('logging')
    logging.getLogger('discord.gateway').setLevel(logging.INFO)
    logging.getLogger('discord.client').setLevel(logging.INFO)
    logger.info(
        'Connecting to Discord gateway (WebSocket; HTTP-only checks are not enough)'
    )
    bot.run(os.environ['DISCORD_TOKEN'])
