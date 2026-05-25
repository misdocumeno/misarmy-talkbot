"""Discord gateway event wiring for the talkbot.

Discord.py scatters reactions across many hooks; this module keeps guild lifecycle,
messages, and voice updates in one place so follow rules, session lookup, and first-boot
work (database, config, metrics) stay out of slash commands and domain modules. That
matters when the gateway reconnects: we need a single place to clear transients and
nudge voice recovery without duplicating guild identifiers through the codebase.
"""

from __future__ import annotations

import asyncio
import os

import discord

from misarmy_talkbot.app.follow_ops import message_for_followed
from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.config.command import on_config_mention
from misarmy_talkbot.infra.config.config import (
    default_config,
    get_config_json,
    set_default_config,
    update_config,
)
from misarmy_talkbot.infra.database.database import create_tables
from misarmy_talkbot.observability.debugpy_boot import start_debugpy_if_enabled
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.observability.trace import step
from misarmy_talkbot.utils import is_deafened

_first_ready_done = False
_metrics_snapshot_stop: asyncio.Event | None = None


def metrics_snapshot_stop_event() -> asyncio.Event:
    """Return the event shared with SIGTERM that stops the metrics snapshot loop.

    Created lazily so importing this module never starts background tasks. The same
    object must be used from ``on_ready`` and shutdown: otherwise the loop keeps
    running after ``dispose_all`` or stops early while sessions are still alive.
    """
    global _metrics_snapshot_stop
    if _metrics_snapshot_stop is None:
        _metrics_snapshot_stop = asyncio.Event()
    return _metrics_snapshot_stop


async def _sync_slash_commands(bot: discord.Bot) -> None:
    """Register slash commands with Discord after login (not on every reconnect)."""
    logger.info('slash_sync ENTER')
    try:
        await bot.sync_commands()
        logger.info('slash_sync EXIT')
    except Exception:
        logger.exception('slash_sync_failed')


async def _first_boot(bot: discord.Bot) -> None:
    """DB + per-guild config after login; must not block the gateway READY handler."""
    logger.info('first_boot ENTER guild_count=%s', len(bot.guilds))
    try:
        await create_tables()
        await _load_guild_config(None)
        for guild in bot.guilds:
            await _load_guild_config(guild)
        interval = float(os.getenv('METRICS_SNAPSHOT_INTERVAL_SECONDS', '300'))
        asyncio.create_task(
            MetricsRegistry.instance().snapshot_loop_task(
                metrics_snapshot_stop_event(), interval
            ),
            name='metrics_snapshot_loop',
        )
        logger.info('first_boot EXIT guild_count=%s', len(bot.guilds))
    except Exception:
        logger.exception('first_boot_failed guild_count=%s', len(bot.guilds))


async def _load_guild_config(guild: discord.Guild | None) -> None:
    """Hydrate in-memory config from storage, falling back if the row is invalid.

    Operators sometimes hand-edit JSON; a bad guild row must not brick startup or
    guild join, so we reset to defaults and log instead of leaving the guild without a bot.
    """
    from pydantic_core import ValidationError

    try:
        await update_config(
            await get_config_json(guild) or default_config, guild
        )
    except (ValueError, ValidationError):
        await set_default_config(guild)
        text = (
            'global config'
            if guild is None
            else f'config for {guild.name} ({guild.id})'
        )
        logger.error('Failed to load %s, using default config instead.', text)


def register_events(bot: discord.Bot) -> None:
    """Register all ``bot`` event handlers used by the talkbot.

    Voice and follow reactions live here rather than next to slash commands so
    gateway-driven flows (resume, disconnect, member voice moves) stay one linear
    story that matches how incidents are debugged.
    """
    metrics = MetricsRegistry.instance()
    sessions = GuildSessionRegistry.instance()

    @bot.event
    async def on_guild_join(guild: discord.Guild) -> None:
        logger.info('Joined %s (%s)', guild.name, guild.id)
        await _load_guild_config(guild)

    @bot.event
    async def on_guild_remove(guild: discord.Guild) -> None:
        logger.info('Left %s (%s)', guild.name, guild.id)
        await sessions.dispose(guild.id)
        FollowRegistry.instance().discard_guild(guild.id)

    @bot.event
    async def on_message(message: discord.Message) -> None:
        if message.guild is None or not isinstance(
            message.author, discord.Member
        ):
            return
        if bot.user in message.mentions and await on_config_mention(message):
            return
        if is_deafened(message.author):
            return
        await message_for_followed(bot, message)

    @bot.event
    async def on_message_edit(
        _before: discord.Message, after: discord.Message
    ) -> None:
        if after.guild is None:
            return
        guild_session = sessions.get(after.guild.id)
        if guild_session is not None:
            await guild_session.engine.on_message_edit(after)

    @bot.event
    async def on_message_delete(message: discord.Message) -> None:
        if message.guild is None:
            return
        guild_session = sessions.get(message.guild.id)
        if guild_session is not None:
            await guild_session.engine.on_message_delete(message)

    @bot.event
    async def on_voice_state_update(
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if before.channel == after.channel:
            return

        guild_id = member.guild.id
        step(
            guild_id,
            'events',
            'voice_state_update',
            'POINT',
            member_id=member.id,
            is_bot=member.id == bot.user.id if bot.user else False,
            before_channel=before.channel.id if before.channel else None,
            after_channel=after.channel.id if after.channel else None,
        )
        follow_registry = FollowRegistry.instance()
        disconnect_supervisor = DisconnectSupervisor.instance()

        if member == bot.user:
            if after.channel is None:
                guild_session = sessions.get(guild_id)
                if guild_session is not None:
                    teardown = guild_session.pilot.last_teardown_context()
                    teardown_reason = teardown.get('reason')
                    teardown_age_s = teardown.get('age_s')
                    likely_our_teardown = (
                        isinstance(teardown_age_s, int | float)
                        and teardown_age_s < 5.0
                        and teardown_reason is not None
                    )
                    logger.warning(
                        'voice_bot_left_channel guild_id=%s before_channel=%s '
                        'likely_our_teardown=%s last_teardown_reason=%s teardown_age_s=%s',
                        guild_id,
                        before.channel.id if before.channel else None,
                        likely_our_teardown,
                        teardown_reason,
                        teardown_age_s,
                    )
                    guild_session.pilot.schedule_recover()
            elif before.channel is not None:
                await reconcile_master_channel(bot, guild_id)
            return

        if not follow_registry.is_followed(guild_id, member.id):
            return

        if after.channel is None:

            async def should_drop() -> bool:
                guild_snapshot = bot.get_guild(guild_id)
                if guild_snapshot is None:
                    return False
                member_refresh = guild_snapshot.get_member(member.id)
                if not follow_registry.is_followed(guild_id, member.id):
                    return False
                return (
                    member_refresh is None
                    or member_refresh.voice is None
                    or member_refresh.voice.channel is None
                )

            await disconnect_supervisor.schedule_drop(
                guild_id,
                member.id,
                None,
                should_unfollow=should_drop,
                follow_registry=follow_registry,
                metrics_hook=lambda: metrics.inc(
                    'grace_drops_confirmed_total', guild_id
                ),
            )
            return

        if before.channel is None:
            await disconnect_supervisor.cancel_drop(guild_id, member.id)
            return

        master_user_id = follow_registry.master(guild_id)
        if member.id != master_user_id:
            follow_registry.unfollow(guild_id, member.id)
            if not follow_registry.is_tracked(guild_id):
                await sessions.dispose(guild_id)
            return

        follow_registry.remove_non_masters(guild_id)
        await disconnect_supervisor.cancel_all_for_guild(guild_id)
        guild_session = sessions.get(guild_id)
        if guild_session is None:
            return
        await guild_session.engine.clear()
        error = await guild_session.pilot.move(after.channel.id)
        if isinstance(error, PermissionError):
            follow_registry.unfollow(guild_id, member.id)
            await reconcile_master_channel(bot, guild_id)

    @bot.event
    async def on_ready() -> None:
        global _first_ready_done
        logger.info('Logged in as %s (guilds=%s)', bot.user, len(bot.guilds))
        asyncio.get_running_loop().call_soon(start_debugpy_if_enabled)
        if not _first_ready_done:
            _first_ready_done = True
            asyncio.create_task(_first_boot(bot), name='first_boot')
            asyncio.create_task(_sync_slash_commands(bot), name='slash_sync')
        metrics.inc_process('on_ready_count')
        for guild_session in sessions.iter_alive():
            guild_session.pilot.set_gateway_transient(False)

    @bot.event
    async def on_connect() -> None:
        logger.info('gateway_connect')

    @bot.event
    async def on_resume() -> None:
        metrics.inc_process('on_resume_count')
        step(
            None,
            'events',
            'gateway_resume',
            'POINT',
            guild_sessions=len(list(sessions.iter_alive())),
        )
        for guild_session in sessions.iter_alive():
            guild_session.pilot.set_gateway_transient(False)

    @bot.event
    async def on_disconnect() -> None:
        metrics.inc_process('on_disconnect_count')
        ws = getattr(bot, 'ws', None)
        close_code = (
            getattr(ws, 'close_code', None) if ws is not None else None
        )
        logger.warning(
            'gateway_disconnect close_code=%s is_ready=%s guild_count=%s',
            close_code,
            bot.is_ready(),
            len(bot.guilds),
        )
        if not bot.is_ready():
            logger.warning(
                'Never reached READY — slash commands will not run. '
                'Check VPN or tunnel software (e.g. Cloudflare WARP on Windows with WSL mirrored networking).'
            )
        step(
            None,
            'events',
            'gateway_disconnect',
            'POINT',
            guild_sessions=len(list(sessions.iter_alive())),
        )
        for guild_session in sessions.iter_alive():
            guild_session.pilot.set_gateway_transient(True)
