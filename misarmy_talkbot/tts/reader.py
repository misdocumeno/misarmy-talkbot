import discord
from .speaker import GuildSpeaker
from ..utils import is_deafened
from ..logger import logger
from typing import cast


class FollowedMember:
    def __init__(self, member: discord.Member):
        self._member = member
        self.following_since = discord.utils.utcnow()
        self.ignored_channels: set[discord.abc.Messageable] = set()

    @property
    def member(self):
        return self._member


class GuildReader:
    _guild: discord.Guild
    _speaker: GuildSpeaker
    _master: discord.Member | None
    _following: dict[discord.Member, FollowedMember]

    def __init__(self, guild: discord.Guild):
        self._guild = guild
        self._speaker = GuildSpeaker(guild)
        self._master = None
        self._following = {}

    @property
    def speaker(self):
        return self._speaker

    async def follow(self, member: discord.Member) -> tuple[discord.Colour, str]:
        """
        Starts speaking out loud messages from this member.
        Returns a translation msgid to reply to the follow command.
        """
        if member in self._following:
            return discord.Colour.red(), 'already_following'

        if member.voice is None or member.voice.channel is None:
            return discord.Colour.red(), 'not_in_voice_channel'

        if self._guild.voice_client is None or self._guild.voice_client.channel is None:
            self._master = member
            await self._speaker.set_channel(member.voice.channel)
        elif member.voice.channel != self._guild.voice_client.channel:
            return discord.Colour.red(), 'not_in_same_voice_channel'

        self._following[member] = FollowedMember(member)

        return discord.Colour.dark_purple(), 'follow_success'

    async def unfollow(self, member: discord.Member) -> tuple[discord.Colour, str]:
        """
        Stops speaking out loud messages from this member.
        If the member was the master, a new master will be chosen based on following order.
        """
        if member not in self._following:
            return discord.Colour.red(), 'not_following'

        del self._following[member]

        if member != self._master:
            return discord.Colour.dark_purple(), 'unfollow_success'

        # get a new master
        if len(self._following):
            self._master = min(self._following.values(), key=lambda m: m.following_since).member
            return discord.Colour.dark_purple(), 'unfollow_success'

        self._master = None
        await self._speaker.set_channel(None)
        return discord.Colour.dark_purple(), 'unfollow_success'

    def ignore(self, member: discord.Member, channel: discord.abc.Messageable) -> tuple[discord.Colour, str]:
        """Toggles speaking out loud messages from this member in this channel."""
        if member not in self._following:
            return discord.Colour.red(), 'not_following'

        if channel in self._following[member].ignored_channels:
            self._following[member].ignored_channels.remove(channel)
            return discord.Colour.dark_purple(), 'un_ignore_success'

        self._following[member].ignored_channels.add(channel)
        return discord.Colour.dark_purple(), 'ignore_success'

    async def on_message(self, message: discord.Message):
        member = cast(discord.Member, message.author)
        if (
            member in self._following
            and not is_deafened(member)
            and message.channel not in self._following[member].ignored_channels
        ):
            logger.debug(f'Reading {message.content!r} from {member} in {message.guild}')
            await self._speaker.on_message(message)

    async def on_voice_connect(self, member: discord.Member):
        pass

    async def on_voice_disconnect(self, member: discord.Member):
        if member in self._following:
            await self.unfollow(member)

    async def on_voice_move(self, member: discord.Member):
        if member not in self._following:
            return

        if member != self._master or member.voice is None or member.voice.channel is None:
            await self.unfollow(member)
            return

        self._following.clear()
        self._following[member] = FollowedMember(member)
        await self._speaker.set_channel(member.voice.channel)

    async def go_to_master(self):
        if self._master is not None and self._master.voice is not None:
            await self._speaker.set_channel(self._master.voice.channel)
