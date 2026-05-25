"""Discord and Lavalink event wiring for the talkbot.

Discord gateway events drive follow/grace/session lookup; wavelink events drive
playback queue advance. Voice connection health is owned by Lavalink/wavelink:
nothing in here reconnects, polls voice WS state, or watches close codes.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import discord

from misarmy_talkbot.app.follow_ops import message_for_followed
from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.audio_storage import AudioStorage
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
from misarmy_talkbot.utils import is_deafened

if TYPE_CHECKING:
    import wavelink
    from discord.ext import commands

    from misarmy_talkbot.core.session.session import GuildSession

_first_ready_done = False
_metrics_snapshot_stop: asyncio.Event | None = None
_audio_janitor_stop: asyncio.Event | None = None


def metrics_snapshot_stop_event() -> asyncio.Event:
    """Event shared with SIGTERM that stops the metrics snapshot loop."""
    global _metrics_snapshot_stop
    if _metrics_snapshot_stop is None:
        _metrics_snapshot_stop = asyncio.Event()
    return _metrics_snapshot_stop


def audio_janitor_stop_event() -> asyncio.Event:
    """Event shared with SIGTERM that stops the audio storage janitor loop."""
    global _audio_janitor_stop
    if _audio_janitor_stop is None:
        _audio_janitor_stop = asyncio.Event()
    return _audio_janitor_stop


async def _first_boot(bot: commands.Bot) -> None:
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
        asyncio.create_task(
            AudioStorage.instance().janitor_loop(audio_janitor_stop_event()),
            name='audio_janitor',
        )
        logger.info('first_boot EXIT guild_count=%s', len(bot.guilds))
    except Exception:
        logger.exception('first_boot_failed guild_count=%s', len(bot.guilds))


async def _load_guild_config(guild: discord.Guild | None) -> None:
    """Hydrate in-memory config from storage, falling back if the row is invalid.

    Operators sometimes hand-edit JSON; a bad guild row must not brick startup
    or guild join, so we reset to defaults and log instead.
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


def register_events(bot: commands.Bot) -> None:
    """Register all ``bot`` event handlers used by the talkbot.

    Voice and follow reactions live here rather than next to slash commands so
    gateway-driven flows stay one linear story.
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
        follow_registry = FollowRegistry.instance()
        disconnect_supervisor = DisconnectSupervisor.instance()

        # The bot's own voice transitions are handled by Lavalink/wavelink; we
        # only care about followed users here. (Lavalink resyncs the player
        # when Discord moves the bot, we do not need to react.)
        if member == bot.user:
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

        # Master moved channels - resync the player and clear in-flight queue
        # because the playback target just changed.
        follow_registry.remove_non_masters(guild_id)
        await disconnect_supervisor.cancel_all_for_guild(guild_id)
        guild_session = sessions.get(guild_id)
        if guild_session is None:
            return
        await guild_session.engine.clear()
        error = await guild_session.lavalink.ensure_connected_to(
            after.channel.id
        )
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

    @bot.event
    async def on_resumed() -> None:
        metrics.inc_process('on_resume_count')

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
                'Never reached READY - slash commands will not run. '
                'Check VPN or tunnel software (e.g. Cloudflare WARP on '
                'Windows with WSL mirrored networking).'
            )

    # ---- wavelink events: the only signals we use to advance the queue ----

    @bot.event
    async def on_wavelink_node_ready(
        payload: wavelink.NodeReadyEventPayload,
    ) -> None:
        logger.info(
            'lavalink_node_ready node=%s session_id=%s resumed=%s',
            payload.node.identifier,
            payload.session_id,
            payload.resumed,
        )

    @bot.event
    async def on_wavelink_node_closed(node: wavelink.Node) -> None:
        # Wavelink reconnects automatically; we just log.
        logger.warning(
            'lavalink_node_closed node=%s status=%s',
            node.identifier,
            node.status,
        )

    @bot.event
    async def on_wavelink_track_start(
        payload: wavelink.TrackStartEventPayload,
    ) -> None:
        guild_id = _payload_guild_id(payload)
        if guild_id is not None:
            metrics.inc('lavalink_track_start_total', guild_id)

    @bot.event
    async def on_wavelink_track_end(
        payload: wavelink.TrackEndEventPayload,
    ) -> None:
        guild_session = _session_for_payload(payload)
        if guild_session is not None:
            guild_session.engine.on_track_end(payload)

    @bot.event
    async def on_wavelink_track_exception(
        payload: wavelink.TrackExceptionEventPayload,
    ) -> None:
        guild_session = _session_for_payload(payload)
        if guild_session is not None:
            guild_session.engine.on_track_exception(payload)

    @bot.event
    async def on_wavelink_track_stuck(
        payload: wavelink.TrackStuckEventPayload,
    ) -> None:
        guild_session = _session_for_payload(payload)
        if guild_session is not None:
            guild_session.engine.on_track_stuck(payload)

    @bot.event
    async def on_wavelink_websocket_closed(
        payload: wavelink.WebsocketClosedEventPayload,
    ) -> None:
        guild_id = _payload_guild_id(payload)
        logger.warning(
            'lavalink_ws_closed guild_id=%s code=%s reason=%s by_remote=%s',
            guild_id,
            payload.code,
            payload.reason,
            payload.by_remote,
        )


async def _sync_slash_commands(bot: commands.Bot) -> None:
    """Register slash commands with Discord after login (not on every reconnect)."""
    logger.info('slash_sync ENTER')
    try:
        from misarmy_talkbot.args import args

        dev_guild = discord.Object(id=args.dev_guild)
        await bot.tree.sync(guild=dev_guild)
        await bot.tree.sync()
        logger.info('slash_sync EXIT')
    except Exception:
        logger.exception('slash_sync_failed')


def _payload_guild_id(payload: object) -> int | None:
    player = getattr(payload, 'player', None)
    if player is None:
        return None
    guild = getattr(player, 'guild', None)
    if guild is None:
        return None
    return guild.id


def _session_for_payload(payload: object) -> GuildSession | None:
    guild_id = _payload_guild_id(payload)
    if guild_id is None:
        return None
    return GuildSessionRegistry.instance().get(guild_id)
