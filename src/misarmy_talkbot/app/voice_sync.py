"""Voice channel alignment between the follow master and the Lavalink session.

Commands and events both need the same "follow the master" behavior; this module
keeps that rule in one place so permission failures are handled consistently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.session.registry import GuildSessionRegistry

if TYPE_CHECKING:
    from discord.ext import commands


async def drop_follower(
    bot: commands.Bot, guild_id: int, user_id: int
) -> None:
    """Unfollow ``user_id``; dispose the session if nobody is left, else re-sync.

    Every path that removes a follower must end here so the bot never stays in
    voice with a session whose follow set is already empty.
    """
    follow_registry = FollowRegistry.instance()
    follow_registry.unfollow(guild_id, user_id)
    if not follow_registry.is_tracked(guild_id):
        await GuildSessionRegistry.instance().dispose(guild_id)
        return
    await reconcile_master_channel(bot, guild_id)


async def reconcile_master_channel(bot: commands.Bot, guild_id: int) -> None:
    """Move the bot into the master's current voice channel, or unfollow if impossible.

    Called after follow changes and grace handling so the bot does not sit in a
    stale channel while the registry already points at a different master or
    channel.
    """
    follow_registry = FollowRegistry.instance()
    master_user_id = follow_registry.master(guild_id)
    guild_session = GuildSessionRegistry.instance().get(guild_id)
    if guild_session is None or master_user_id is None:
        return
    guild = bot.get_guild(guild_id)
    if guild is None:
        return
    master_member = guild.get_member(master_user_id)
    if (
        master_member is None
        or master_member.voice is None
        or master_member.voice.channel is None
    ):
        return
    error = await guild_session.lavalink.ensure_connected_to(
        master_member.voice.channel.id
    )
    if isinstance(error, PermissionError):
        await drop_follower(bot, guild_id, master_user_id)
