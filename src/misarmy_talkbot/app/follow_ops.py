"""Slash-command helpers for follow / ignore and message-to-TTS routing.

These functions sit between Discord API objects and the follow registry because
commands must stay thin: all branching on voice presence, session lifetime, and
registry promotion lives here so ``commands.py`` only deals with interaction flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord

from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioMessage
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from discord.ext import commands


async def follow_user(
    _bot: commands.Bot, guild: discord.Guild, member: discord.Member
) -> tuple[discord.Colour, str]:
    """Record follow intent and connect Lavalink to the member's voice channel.

    Voice connect happens only after registry writes so a failed join can roll
    follow state back; otherwise the guild would think it is following someone
    the bot never joined.
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
        error = await guild_session.lavalink.ensure_connected_to(
            target_channel_id
        )
        if error is not None:
            follow_registry.unfollow(guild_id, user_id)
            await sessions.dispose(guild_id)
            return discord.Colour.red(), 'not_in_same_voice_channel'
        return discord.Colour.dark_purple(), 'follow_success'

    current_channel = (
        guild_session.lavalink.player.channel
        if guild_session.lavalink.player is not None
        else None
    )
    current_channel_id = current_channel.id if current_channel else None
    if (
        current_channel_id is not None
        and target_channel_id != current_channel_id
        and master_user_id != user_id
    ):
        return discord.Colour.red(), 'not_in_same_voice_channel'

    error = await guild_session.lavalink.ensure_connected_to(target_channel_id)
    if error is not None:
        return discord.Colour.red(), 'not_in_same_voice_channel'

    if not follow_registry.follow(guild_id, user_id):
        return discord.Colour.red(), 'already_following'
    return discord.Colour.dark_purple(), 'follow_success'


async def unfollow_user(
    bot: commands.Bot, guild: discord.Guild, member: discord.Member
) -> tuple[discord.Colour, str]:
    """Remove follow intent; dispose the guild session or re-sync remaining master."""
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
    _bot: commands.Bot, message: discord.Message
) -> None:
    """Enqueue a followed user's message for TTS if the guild has an active session."""
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

    await guild_session.engine.enqueue(AudioMessage(message))
