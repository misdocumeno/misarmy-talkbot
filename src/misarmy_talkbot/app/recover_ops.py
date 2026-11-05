"""Admin recovery actions: fix runtime without clearing user-visible follow state."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import discord
import wavelink

from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

if TYPE_CHECKING:
    from discord.ext import commands


async def recover_diagnose(
    _bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    follow = FollowRegistry.instance()
    session = GuildSessionRegistry.instance().get(guild_id)
    master = follow.master(guild_id)
    tracked = follow.is_tracked(guild_id)
    lines = [
        f'master={master}',
        f'follow_tracked={tracked}',
        f'session_alive={session is not None}',
    ]
    if session is not None:
        engine = session.engine
        head = engine.queue.head()
        lines.extend([
            f'queue_depth={len(engine.queue)}',
            f'gen_tasks={engine.gen_task_count}',
            f'current_message='
            f'{engine.current.original.id if engine.current else None}',
            f'head_state={head.state.name if head else None}',
            f'head_message={head.original.id if head else None}',
            f'position_ms={engine.last_position_ms}',
            f'stuck_for_s={engine.note_progress_for_stall()}',
            f'track_length_ms={engine.current_track_length_ms}',
            f'player={session.lavalink.player is not None}',
        ])
    metrics = MetricsRegistry.instance().snapshot_guild_embed_fields(guild_id)
    if metrics:
        lines.append(
            'metrics=' + ', '.join(f'{n}={v}' for n, v in metrics[:8])
        )
    return discord.Colour.dark_purple(), '\n'.join(lines)


async def recover_drop_head(
    _bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    session = GuildSessionRegistry.instance().get(guild_id)
    if session is None:
        return discord.Colour.red(), 'No active session for this guild.'
    ok = await session.engine.drop_head()
    if not ok:
        return discord.Colour.red(), 'Queue is empty.'
    logger.warning('recover_drop_head guild_id=%s', guild_id)
    return (
        discord.Colour.dark_purple(),
        'Dropped queue head / skipped current.',
    )


async def recover_clear_queue(
    _bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    session = GuildSessionRegistry.instance().get(guild_id)
    if session is None:
        return discord.Colour.red(), 'No active session for this guild.'
    await session.engine.clear()
    logger.warning('recover_clear_queue guild_id=%s', guild_id)
    return (
        discord.Colour.dark_purple(),
        'Cleared TTS queue (follow unchanged).',
    )


async def recover_reset_speak(
    _bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    session = GuildSessionRegistry.instance().get(guild_id)
    if session is None:
        return discord.Colour.red(), 'No active session for this guild.'
    await session.engine.restart_speak_loop()
    logger.warning('recover_reset_speak guild_id=%s', guild_id)
    return (
        discord.Colour.dark_purple(),
        'Restarted speak loop (queue and follow unchanged).',
    )


async def recover_reconnect_voice(
    bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    session = GuildSessionRegistry.instance().get(guild_id)
    if session is None:
        return discord.Colour.red(), 'No active session for this guild.'
    await session.lavalink.disconnect()
    await reconcile_master_channel(bot, guild_id)
    logger.warning('recover_reconnect_voice guild_id=%s', guild_id)
    return (
        discord.Colour.dark_purple(),
        'Reconnected voice to master channel (follow unchanged).',
    )


async def recover_reset_guild(
    bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    """Recreate GuildSession + player; preserve FollowRegistry data."""
    sessions = GuildSessionRegistry.instance()
    follow = FollowRegistry.instance()
    was_tracked = follow.is_tracked(guild_id)
    master = follow.master(guild_id)
    await sessions.dispose(guild_id)
    if not was_tracked:
        return (
            discord.Colour.dark_purple(),
            'Disposed session; no follow state was registered.',
        )
    # Follow registry untouched; rebuild runtime and re-sync voice.
    await sessions.get_or_create(guild_id)
    await reconcile_master_channel(bot, guild_id)
    logger.warning(
        'recover_reset_guild guild_id=%s master=%s follow_preserved=1',
        guild_id,
        master,
    )
    return (
        discord.Colour.dark_purple(),
        'Recreated guild session and re-synced voice; follow list preserved.',
    )


async def recover_reconnect_node(
    bot: commands.Bot, _guild_id: int
) -> tuple[discord.Colour, str]:
    """Reconnect shared Lavalink node; preserve all follow registries."""
    host = os.getenv('LAVALINK_HOST', 'lavalink')
    port = os.getenv('LAVALINK_PORT', '2333')
    password = os.getenv('LAVALINK_PASSWORD', 'youshallnotpass')
    try:
        await wavelink.Pool.close()
    except Exception:
        logger.exception('recover_reconnect_node_close_failed')
    node = wavelink.Node(
        uri=f'http://{host}:{port}',
        password=password,
        identifier='main',
    )
    await wavelink.Pool.connect(nodes=[node], client=bot)
    for session in GuildSessionRegistry.instance().iter_alive():
        await reconcile_master_channel(bot, session.guild_id)
    logger.warning('recover_reconnect_node host=%s port=%s', host, port)
    return (
        discord.Colour.dark_purple(),
        'Reconnected Lavalink node and re-synced active guild voices.',
    )


async def recover_dump(
    bot: commands.Bot, guild_id: int
) -> tuple[discord.Colour, str]:
    colour, body = await recover_diagnose(bot, guild_id)
    tip = (
        '\n\nTip: inspect Grafana Tempo/Loki for guild_id='
        f'{guild_id} around the stall.'
    )
    return colour, body + tip
