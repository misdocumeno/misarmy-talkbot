"""Application entry: construct the Discord bot, connect Lavalink, run it.

This layer exists so ``core`` and ``infra`` stay free of global wiring; the bot
instance is the composition root where registries meet Discord callbacks and
where SIGTERM can drain sessions before the process exits.

Boot is split so the slow, CPU-bound application imports overlap the I/O-bound
Discord HTTP login (the gateway handshake is the other large wait, which is
spent in ``connect``)::

    login (HTTP, I/O)        ──────────────┐
    warm app imports (thread, CPU) ────────┴─► attach commands/events
                                              ─► lavalink connect (task)
                                              ─► gateway connect (I/O) ─► READY

Commands and event handlers must be attached before ``connect`` so that no
gateway or wavelink event is dispatched into an empty listener set.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import signal
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    import types

from misarmy_talkbot.observability.health import set_health_probe
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.startup import startup_tracker
from misarmy_talkbot.observability.tracing import setup_tracing

# Modules pulled in while the Discord login is in flight. Importing them here
# (in a worker thread) moves their cost off the critical path. The names are
# bound later by ``_attach_app``, which then only hits ``sys.modules``.
_APP_MODULES = (
    'wavelink',
    'misarmy_talkbot.app.commands',
    'misarmy_talkbot.app.events',
    'misarmy_talkbot.app.voice_sync',
    'misarmy_talkbot.app.voice_watchdog',
    'misarmy_talkbot.core.follow.grace',
    'misarmy_talkbot.core.follow.registry',
    'misarmy_talkbot.core.session.registry',
    'misarmy_talkbot.infra.audio_http',
    'misarmy_talkbot.observability.metrics_http',
    'misarmy_talkbot.observability.stall',
)


def _warm_app_imports() -> None:
    for name in _APP_MODULES:
        importlib.import_module(name)


def _new_bot() -> commands.Bot:
    """Bot shell with intents only; handlers and commands are attached later."""
    intents = discord.Intents.default()
    intents.members = True
    intents.message_content = True
    intents.voice_states = True
    return commands.Bot(command_prefix='!', intents=intents)


def _wire_singletons(bot: commands.Bot) -> None:
    """Connect follow and session singletons to Discord-facing callbacks.

    Callbacks are registered here (not inside domain classes) so ``core`` does
    not import ``app`` and tests can reset singletons without constructing a
    full bot.
    """
    from misarmy_talkbot.app.voice_sync import reconcile_master_channel
    from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
    from misarmy_talkbot.core.follow.registry import FollowRegistry
    from misarmy_talkbot.core.session.registry import GuildSessionRegistry

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


def _attach_app(bot: commands.Bot) -> None:
    """Wire singletons, slash commands and event handlers. Must run before connect."""
    from misarmy_talkbot.app.commands import register_commands
    from misarmy_talkbot.app.events import register_events

    _wire_singletons(bot)
    register_commands(bot)
    register_events(bot)


async def _connect_lavalink(bot: commands.Bot) -> None:
    """Open the Lavalink pool. Runs after events are attached (node_ready needs them)."""
    import wavelink

    startup_tracker().begin('lavalink_connect')
    host = os.getenv('LAVALINK_HOST', 'lavalink')
    port = os.getenv('LAVALINK_PORT', '2333')
    password = os.getenv('LAVALINK_PASSWORD', 'youshallnotpass')
    node = wavelink.Node(
        uri=f'http://{host}:{port}',
        password=password,
        identifier='main',
    )
    try:
        await wavelink.Pool.connect(nodes=[node], client=bot)
        logger.info(
            'lavalink_pool_connect_initiated host=%s port=%s', host, port
        )
    except Exception:
        logger.exception(
            'lavalink_pool_connect_failed host=%s port=%s', host, port
        )


def _install_sigterm(
    bot: commands.Bot, loop: asyncio.AbstractEventLoop
) -> None:
    """Drain sessions and Lavalink, then close the bot, on SIGTERM."""

    def signal_handler(_signum: int, _frame: types.FrameType | None) -> None:
        async def _close() -> None:
            import wavelink

            from misarmy_talkbot.app.events import (
                audio_janitor_stop_event,
                metrics_snapshot_stop_event,
            )
            from misarmy_talkbot.app.voice_watchdog import (
                voice_watchdog_stop_event,
            )
            from misarmy_talkbot.core.session.registry import (
                GuildSessionRegistry,
            )
            from misarmy_talkbot.infra.audio_http import AudioHttpServer
            from misarmy_talkbot.observability.metrics_http import (
                stop_metrics_http,
            )
            from misarmy_talkbot.observability.stall import stall_stop_event

            metrics_snapshot_stop_event().set()
            audio_janitor_stop_event().set()
            stall_stop_event().set()
            voice_watchdog_stop_event().set()
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
            try:
                await AudioHttpServer.instance().stop()
            except Exception:
                logger.exception('shutdown_audio_http')
            try:
                await stop_metrics_http()
            except Exception:
                logger.exception('shutdown_metrics_http')
            await bot.close()

        loop.create_task(_close())

    signal.signal(signal.SIGTERM, signal_handler)


async def _serve(bot: commands.Bot, token: str) -> None:
    tracker = startup_tracker()
    loop = asyncio.get_running_loop()

    async def warm_imports() -> None:
        with tracker.phase('imports_app'):
            await asyncio.to_thread(_warm_app_imports)

    async with bot:
        tracker.begin('discord_login')
        # Login is HTTP-only; the warm-up runs in a thread while it waits.
        # ``login`` also runs ``setup_hook`` (no app work happens there).
        # ``discord_login`` is closed by ``on_ready`` (end of gateway handshake).
        await asyncio.gather(bot.login(token), warm_imports())

        _install_sigterm(bot, loop)
        with tracker.phase('bot_build'):
            _attach_app(bot)
        asyncio.create_task(_connect_lavalink(bot), name='lavalink_connect')

        logger.info(
            'Connecting to Discord gateway (WebSocket; HTTP-only checks are not enough)'
        )
        await bot.connect(reconnect=True)


def run_bot() -> None:
    """Start the bot process (blocking) and register cooperative shutdown on SIGTERM."""
    setup_tracing()
    # Same defaults ``discord.Client.run`` applies, which this replaces.
    discord.utils.setup_logging()
    for name in ('discord.gateway', 'discord.client'):
        logging.getLogger(name).setLevel(logging.INFO)

    bot = _new_bot()
    tracker = startup_tracker()
    set_health_probe(lambda: bot.is_ready() and tracker.ready_at_s is not None)
    try:
        asyncio.run(_serve(bot, os.environ['DISCORD_TOKEN']))
    except KeyboardInterrupt:
        return
