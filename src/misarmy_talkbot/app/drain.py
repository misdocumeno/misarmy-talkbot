"""Zero-disruption restart: drain playback, snapshot, exit; restore on boot.

Why ``os._exit``: a normal close sends Lavalink teardown and voice disconnect
events, which Discord shows as the bot leaving the channel. We instead commit
the snapshot, flush logs, and exit immediately. Discord and Lavalink keep the
voice session for a short timeout, and the restarted process reconciles it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from typing import TYPE_CHECKING

import discord

from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioMessage
from misarmy_talkbot.core.playback.drain import (
    DrainController,
    GuildDrainState,
    IgnoredChannels,
    PendingItem,
    SnapshotError,
    decode_snapshot,
    encode_snapshot,
)
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.database.drain_snapshot import (
    delete_snapshot,
    load_latest_snapshot,
    save_snapshot,
)
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from discord.ext import commands

    from misarmy_talkbot.core.session.session import GuildSession

_lavalink_ready: asyncio.Event | None = None


def lavalink_ready_event() -> asyncio.Event:
    """Set by ``on_wavelink_node_ready``; restore waits on it before voice work."""
    global _lavalink_ready
    if _lavalink_ready is None:
        _lavalink_ready = asyncio.Event()
    return _lavalink_ready


def _build_states(sessions: list[GuildSession]) -> list[GuildDrainState]:
    follow = FollowRegistry.instance()
    by_guild: dict[int, GuildSession] = {s.guild_id: s for s in sessions}
    guild_ids = sorted(set(by_guild) | set(follow.guild_ids()))
    states: list[GuildDrainState] = []
    for guild_id in guild_ids:
        session = by_guild.get(guild_id)
        follow_view = follow.export_guild(guild_id) or {
            'master': None,
            'members': [],
            'ignored': {},
        }
        voice_channel_id: int | None = None
        pending: tuple[PendingItem, ...] = ()
        if session is not None:
            player = session.lavalink.player
            channel = getattr(player, 'channel', None) if player else None
            voice_channel_id = getattr(channel, 'id', None)
            pending = tuple(
                PendingItem(
                    message_id=item.original.id,
                    channel_id=item.original.channel.id,
                    author_id=item.original.author.id,
                    text=item.content or '',
                )
                for item in session.engine.pending_items()
            )
        states.append(
            GuildDrainState(
                guild_id=guild_id,
                voice_channel_id=voice_channel_id,
                master_user_id=follow_view['master'],
                follower_ids=tuple(follow_view['members']),
                ignored=tuple(
                    IgnoredChannels(user_id=uid, channel_ids=tuple(chs))
                    for uid, chs in follow_view['ignored'].items()
                ),
                pending=pending,
            )
        )
    return states


def flush_and_exit() -> None:
    """Flush logs, then exit without running the normal close path."""
    for handler in logging.getLogger().handlers:
        handler.flush()
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


async def drain_and_exit(*, zombie_after_s: float) -> None:
    """Handle SIGUSR1: finish current items, snapshot, exit with status 0."""
    if not DrainController.instance().request():
        logger.info('drain_already_requested')
        return
    sessions = GuildSessionRegistry.instance().iter_alive()
    logger.warning(
        'drain_start guilds=%s zombie_after_s=%s',
        len(sessions),
        zombie_after_s,
    )
    for session in sessions:
        session.engine.begin_drain()

    waits = await asyncio.gather(
        *(
            session.engine.wait_idle(zombie_after_s=zombie_after_s)
            for session in sessions
        )
    )
    stuck = [
        s.guild_id for s, idle in zip(sessions, waits, strict=True) if not idle
    ]
    if stuck:
        # Stuck guilds keep their queue (including the wedged item) in the
        # snapshot; the next boot retries it.
        logger.warning('drain_zombie_forced guilds=%s', stuck)

    try:
        states = _build_states(sessions)
        item_count = sum(len(state.pending) for state in states)
        await save_snapshot(encode_snapshot(states))
    except Exception:
        # Without a snapshot, exiting would lose the queue. Fall back to the
        # normal SIGTERM path, which is the safe (but noisier) shutdown.
        logger.exception('drain_snapshot_failed; falling back to SIGTERM')
        os.kill(os.getpid(), signal.SIGTERM)
        return

    logger.warning(
        'drain_snapshot_committed guilds=%s pending_items=%s',
        len(states),
        item_count,
    )
    flush_and_exit()


async def restore_drain_snapshot(bot: commands.Bot) -> None:
    """Restore the last drain snapshot once Lavalink is up, then delete it."""
    await lavalink_ready_event().wait()
    row = await load_latest_snapshot()
    if row is None:
        return
    snapshot_id, payload = row
    try:
        states = decode_snapshot(payload)
    except SnapshotError:
        # Keep the row so it can be inspected; do not replay a bad payload.
        logger.exception('drain_snapshot_invalid id=%s', snapshot_id)
        return

    follow = FollowRegistry.instance()
    for state in states:
        try:
            await _restore_guild(bot, state, follow)
        except Exception:
            logger.exception(
                'drain_restore_guild_failed guild_id=%s', state.guild_id
            )

    await delete_snapshot(snapshot_id)
    logger.warning(
        'drain_snapshot_restored id=%s guilds=%s', snapshot_id, len(states)
    )


async def _restore_guild(
    bot: commands.Bot, state: GuildDrainState, follow: FollowRegistry
) -> None:
    guild_id = state.guild_id
    for user_id in state.follower_ids:
        follow.follow(guild_id, user_id)
    if state.master_user_id is not None:
        follow.set_master(guild_id, state.master_user_id)
    for entry in state.ignored:
        for channel_id in entry.channel_ids:
            follow.toggle_ignore_channel(guild_id, entry.user_id, channel_id)
    if not follow.is_tracked(guild_id):
        return

    session = await GuildSessionRegistry.instance().get_or_create(guild_id)
    # Rejoin the master's current channel if they are still in voice.
    await reconcile_master_channel(bot, guild_id)

    for item in state.pending:
        message = await _fetch_message(bot, item)
        if message is None:
            logger.info(
                'drain_restore_message_gone guild_id=%s message_id=%s',
                guild_id,
                item.message_id,
            )
            continue
        await session.engine.enqueue(AudioMessage(message))


async def _fetch_message(
    bot: commands.Bot, item: PendingItem
) -> discord.Message | None:
    try:
        channel = bot.get_channel(item.channel_id) or await bot.fetch_channel(
            item.channel_id
        )
        if not hasattr(channel, 'fetch_message'):
            return None
        return await channel.fetch_message(item.message_id)  # type: ignore[attr-defined]
    except discord.HTTPException:
        return None
