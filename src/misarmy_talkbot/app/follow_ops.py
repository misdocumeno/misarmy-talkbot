"""Slash-command helpers for follow / ignore and message-to-TTS routing.

These functions sit between Discord API objects and the follow registry because
commands must stay thin: all branching on voice presence, session lifetime, and
registry promotion lives here so ``commands.py`` only deals with interaction flow.
"""

from __future__ import annotations

import asyncio
import os
from typing import cast

import discord

from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioMessage
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.trace import step


async def _wait_voice_ready(
    pilot: object, *, guild_id: int, label: str
) -> bool:
    timeout = float(os.getenv('FOLLOW_HEALTHY_WAIT_SECONDS', '20'))
    try:
        await asyncio.wait_for(pilot.wait_until_healthy(), timeout=timeout)  # type: ignore[attr-defined]
        return True
    except TimeoutError:
        logger.warning(
            'follow_voice_wait_timeout guild_id=%s label=%s timeout_s=%s',
            guild_id,
            label,
            timeout,
        )
        return False


async def follow_user(
    _bot: discord.Bot, guild: discord.Guild, member: discord.Member
) -> tuple[discord.Colour, str]:
    """Record follow intent and connect voice, or reject if voice state does not allow it.

    We connect only after registry writes so a failed voice join can roll back follow
    state; otherwise the guild would think it is following someone the bot never joined.
    """
    guild_id = guild.id
    user_id = member.id
    follow_registry = FollowRegistry.instance()
    if follow_registry.is_followed(guild_id, user_id):
        return discord.Colour.red(), 'already_following'

    voice_channel = member.voice.channel if member.voice else None
    if voice_channel is None:
        return discord.Colour.red(), 'not_in_voice_channel'

    sessions = GuildSessionRegistry.instance()
    guild_session = sessions.get(guild_id)
    target_channel_id = voice_channel.id
    master_user_id = follow_registry.master(guild_id)

    if guild_session is None:
        if not follow_registry.follow(guild_id, user_id):
            return discord.Colour.red(), 'already_following'
        follow_registry.set_master(guild_id, user_id)
        guild_session = await sessions.get_or_create(guild_id)
        if not await _wait_voice_ready(
            guild_session.pilot, guild_id=guild_id, label='new_follow'
        ):
            await sessions.dispose(guild_id)
            follow_registry.unfollow(guild_id, user_id)
            return discord.Colour.red(), 'not_in_same_voice_channel'
        error = await guild_session.pilot.ensure_connected_to(
            target_channel_id
        )
        if error is not None:
            follow_registry.unfollow(guild_id, user_id)
            await sessions.dispose(guild_id)
            return discord.Colour.red(), 'not_in_same_voice_channel'
        return discord.Colour.dark_purple(), 'follow_success'

    intended_channel_id = guild_session.pilot.intended_channel_id
    if (
        intended_channel_id is not None
        and target_channel_id != intended_channel_id
        and master_user_id != user_id
    ):
        return discord.Colour.red(), 'not_in_same_voice_channel'

    if not await _wait_voice_ready(
        guild_session.pilot, guild_id=guild_id, label='join_follow'
    ):
        return discord.Colour.red(), 'not_in_same_voice_channel'
    error = await guild_session.pilot.ensure_connected_to(target_channel_id)
    if error is not None:
        return discord.Colour.red(), 'not_in_same_voice_channel'

    if not follow_registry.follow(guild_id, user_id):
        return discord.Colour.red(), 'already_following'
    return discord.Colour.dark_purple(), 'follow_success'


async def unfollow_user(
    bot: discord.Bot, guild: discord.Guild, member: discord.Member
) -> tuple[discord.Colour, str]:
    """Remove follow intent and either drop the guild session or re-sync the remaining master."""
    guild_id = guild.id
    user_id = member.id
    follow_registry = FollowRegistry.instance()
    if not follow_registry.is_followed(guild_id, user_id):
        return discord.Colour.red(), 'not_following'
    follow_registry.unfollow(guild_id, user_id)
    if not follow_registry.is_tracked(guild_id):
        await GuildSessionRegistry.instance().dispose(guild_id)
    else:
        await reconcile_master_channel(bot, guild_id)
    return discord.Colour.dark_purple(), 'unfollow_success'


def ignore_toggle(
    guild: discord.Guild,
    member: discord.Member,
    channel: discord.abc.Messageable,
) -> tuple[discord.Colour, str]:
    """Toggle per-channel ignore for a followed user (text TTS routing)."""
    guild_id = guild.id
    user_id = member.id
    follow_registry = FollowRegistry.instance()
    if not follow_registry.is_followed(guild_id, user_id):
        return discord.Colour.red(), 'not_following'
    channel_id_attr = getattr(channel, 'id', None)
    if channel_id_attr is None:
        return discord.Colour.red(), 'not_following'
    channel_id = int(channel_id_attr)
    toggle_action = follow_registry.toggle_ignore_channel(
        guild_id, user_id, channel_id
    )
    if toggle_action == 'ignored':
        return discord.Colour.dark_purple(), 'ignore_success'
    if toggle_action == 'unignored':
        return discord.Colour.dark_purple(), 'un_ignore_success'
    return discord.Colour.red(), 'not_following'


async def message_for_followed(
    _bot: discord.Bot, message: discord.Message
) -> None:
    """Enqueue a followed user's message for TTS if the guild still has an active session."""
    if message.guild is None:
        return
    guild_id = message.guild.id
    user_id = cast('discord.Member', message.author).id
    follow_registry = FollowRegistry.instance()
    if not follow_registry.is_followed(guild_id, user_id):
        return
    if follow_registry.is_channel_ignored(
        guild_id, user_id, message.channel.id
    ):
        return
    guild_session = GuildSessionRegistry.instance().get(guild_id)
    if guild_session is None:
        logger.warning(
            'dropped_no_session guild_id=%s user_id=%s', guild_id, user_id
        )
        return

    step(
        guild_id,
        'follow',
        'message_enqueue',
        'ENTER',
        user_id=user_id,
        content=message.content[:80],
    )
    await guild_session.engine.enqueue(AudioMessage(message))
    step(
        guild_id,
        'follow',
        'message_enqueue',
        'EXIT',
        user_id=user_id,
        content=message.content[:80],
    )
