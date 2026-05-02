import discord
from .speaker import GuildSpeaker
from ..utils import is_deafened
from ..logger import logger
from ..voice_log import (
    format_discord_voice_state,
    format_follower_voice_slots,
    format_guild_voice_snapshot,
    format_member_voice,
)
from ..reader_debug import audit_all_named_speak_tasks, audit_speak_tasks_for_guild
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
        logger.debug(
            f'GuildReader init guild_snowflake_id={guild.id} guild_obj_id={id(guild)} reader_obj_id={id(self)} '
            f'speaker_obj_id={id(self._speaker)} _guild_is_same_obj={id(self._guild) == id(guild)}'
        )

    @property
    def speaker(self):
        return self._speaker

    def debug_registry_row(self) -> str:
        """Single-line identity for logs (registry / ghost-reader theories)."""
        mid = self._master.id if self._master else None
        mid_oid = id(self._master) if self._master else None
        fid = sorted(m.id for m in self._following)
        raw_following = [
            f'(dict_key_member_obj_id={id(k)},followed_wrap_obj_id={id(v)},user_id={k.id})'
            for k, v in self._following.items()
        ]
        st = self._speaker._speak_task
        return (
            f'guild_snowflake_id={self._guild.id} guild_obj_id={id(self._guild)} reader_obj_id={id(self)} '
            f'speaker_obj_id={id(self._speaker)} speak_task_asyncio_id={id(st)} speak_task_done={st.done()} '
            f'master_user_id={mid} master_member_obj_id={mid_oid} following_user_ids_sorted={fid} '
            f'_following_items_RAW={raw_following}'
        )

    async def follow(self, member: discord.Member) -> tuple[discord.Colour, str]:
        """
        Starts speaking out loud messages from this member.
        Returns a translation msgid to reply to the follow command.
        """
        following_ids = sorted(m.id for m in self._following)
        watcher_ids = sorted(set(following_ids + [member.id]))
        actor_vs = getattr(member, 'voice', None)
        mg = member.guild
        logger.debug(
            f'follow attempt member_obj_id={id(member)} user_id={member.id} '
            f'member.guild_obj_id={id(mg)} self._guild_obj_id={id(self._guild)} same_guild_obj={id(mg) == id(self._guild)} '
            f'guild_snowflake_id={self._guild.id} reader_obj_id={id(self)} speaker_obj_id={id(self._speaker)} '
            f'already={member in self._following} master_user_id={(self._master.id if self._master else None)} '
            f'following_user_ids={following_ids} '
            f'actor_vc={format_discord_voice_state(actor_vs, "actor")} '
            f'followers_cached_voice={format_follower_voice_slots(self._guild, watcher_ids)} '
            f'| SNAP {format_guild_voice_snapshot(self._guild)}'
        )
        if member in self._following:
            watcher_ids = sorted(m.id for m in self._following)
            logger.info(
                f'follow rejected already_following user_id={member.id} | '
                f'{format_follower_voice_slots(self._guild, watcher_ids)} | '
                f'{format_guild_voice_snapshot(self._guild)} | '
                f'{self.debug_registry_row()} | {audit_speak_tasks_for_guild(self._guild.id)}'
            )
            return discord.Colour.red(), 'already_following'

        if member.voice is None or member.voice.channel is None:
            logger.info(
                f'follow rejected not_in_voice_channel user_id={member.id} '
                f'{format_member_voice(member, "actor")} | {format_guild_voice_snapshot(self._guild)}'
            )
            return discord.Colour.red(), 'not_in_voice_channel'

        if self._guild.voice_client is None or self._guild.voice_client.channel is None:
            self._master = member
            logger.info(
                f'follow will connect bot as master user_id={member.id} channel_id={member.voice.channel.id}'
            )
            await self._speaker.set_channel(member.voice.channel)
        elif member.voice.channel != self._guild.voice_client.channel:
            logger.info(
                f'follow rejected not_in_same_voice_channel user_id={member.id} '
                f'user_ch={member.voice.channel.id} bot_ch={self._guild.voice_client.channel.id} '
                f'| {format_guild_voice_snapshot(self._guild)}'
            )
            return discord.Colour.red(), 'not_in_same_voice_channel'

        self._following[member] = FollowedMember(member)
        logger.info(
            f'follow success user_id={member.id} following_count={len(self._following)} '
            f'master_id={(self._master.id if self._master else None)} | '
            f'{format_follower_voice_slots(self._guild, sorted(m.id for m in self._following))} | '
            f'{format_guild_voice_snapshot(self._guild)}'
        )

        return discord.Colour.dark_purple(), 'follow_success'

    async def unfollow(self, member: discord.Member) -> tuple[discord.Colour, str]:
        """
        Stops speaking out loud messages from this member.
        If the member was the master, a new master will be chosen based on following order.
        """
        if member not in self._following:
            logger.debug(f'unfollow rejected not_following user_id={member.id}')
            return discord.Colour.red(), 'not_following'

        was_master = member == self._master
        logger.debug(
            f'unfollow via command user_id={member.id} guild_id={self._guild.id} was_master={was_master} '
            f'following_count_before={len(self._following)}'
        )
        del self._following[member]

        if member != self._master:
            return discord.Colour.dark_purple(), 'unfollow_success'

        # get a new master
        if len(self._following):
            self._master = min(self._following.values(), key=lambda m: m.following_since).member
            logger.info(
                f'unfollow new master user_id={self._master.id} remaining_followers={len(self._following)}'
            )
            return discord.Colour.dark_purple(), 'unfollow_success'

        self._master = None
        logger.info(
            f'unfollow last follower left user_id={member.id}; disconnecting voice | '
            f'{format_guild_voice_snapshot(self._guild)}'
        )
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
        msg_guild = message.guild
        msg_guild_id = msg_guild.id if msg_guild else None
        key_ids = [k.id for k in self._following]
        key_obj_ids = [id(k) for k in self._following]
        in_following = member in self._following
        same_guild_obj = id(msg_guild) == id(self._guild) if msg_guild else None
        logger.debug(
            f'on_message READER_PROBE reader={self.debug_registry_row()} '
            f'msg_obj_id={id(message)} ch_obj_id={id(message.channel)} '
            f'msg_guild_obj_id={id(msg_guild) if msg_guild else None} reader_guild_obj_id={id(self._guild)} '
            f'msg_guild_snowflake_eq={msg_guild_id == self._guild.id} same_guild_python_obj={same_guild_obj} '
            f'author_id={member.id} author_obj_id={id(member)} in_following={in_following} '
            f'following_key_user_ids_RAW={key_ids} following_key_member_obj_ids_RAW={key_obj_ids} '
            f'| {audit_speak_tasks_for_guild(self._guild.id)} | {audit_all_named_speak_tasks()}'
        )
        if member not in self._following:
            return
        preview = message.content.replace('\n', ' ')
        preview = repr(preview if len(preview) <= 100 else preview[:97] + '...')
        mid = message.id
        ch_id = message.channel.id
        if is_deafened(member):
            mv = getattr(member, 'voice', None)
            logger.info(
                f'message IGNORED (deafened) msg_id={mid} guild_id={self._guild.id} user_id={member.id} '
                f'ch_id={ch_id} len={len(message.content)} preview={preview} | '
                f'{format_discord_voice_state(mv, "author")}'
            )
            return
        if message.channel in self._following[member].ignored_channels:
            logger.info(
                f'message IGNORED (ignored_channel) msg_id={mid} guild_id={self._guild.id} '
                f'user_id={member.id} ch_id={ch_id} len={len(message.content)} preview={preview}'
            )
            return

        logger.info(
            f'message -> TTS queue msg_obj_id={id(message)} msg_id={mid} ch_obj_id={id(message.channel)} ch_id={ch_id} '
            f'guild_id={self._guild.id} user_id={member.id} len={len(message.content)} preview={preview}'
        )
        logger.debug(
            f'message follower detail speaker_obj_id={id(self._speaker)} reader_obj_id={id(self)} '
            f'| {self._speaker._voice_debug("reader_before_speaker_on_message")}'
        )
        await self._speaker.on_message(message)

    async def on_voice_connect(self, member: discord.Member):
        logger.debug(
            f'on_voice_connect is_followed={member in self._following} is_master={(member == self._master)} '
            f'{format_member_voice(member, "member")} | SNAP {format_guild_voice_snapshot(self._guild)}'
        )

    async def on_voice_disconnect(self, member: discord.Member):
        if member in self._following:
            logger.info(
                f'on_voice_disconnect unfollow user_id={member.id} guild_id={self._guild.id} '
                f'(followed member left voice) member_vs={format_discord_voice_state(getattr(member, "voice", None), "gw_after")} | '
                f'SNAP {format_guild_voice_snapshot(self._guild)}'
            )
            await self.unfollow(member)
        else:
            logger.debug(
                f'on_voice_disconnect ignored user_id={member.id} not in following set'
            )

    async def on_voice_move(self, member: discord.Member):
        logger.debug(
            f'on_voice_move user_id={member.id} guild_id={self._guild.id} '
            f'is_followed={member in self._following} is_master={(member == self._master)} '
            f'{format_member_voice(member, "member")} | SNAP {format_guild_voice_snapshot(self._guild)}'
        )
        if member not in self._following:
            return

        if member != self._master or member.voice is None or member.voice.channel is None:
            logger.info(
                f'on_voice_move unfollow user_id={member.id} moved_or_not_master '
                f'(master_id={(self._master.id if self._master else None)})'
            )
            await self.unfollow(member)
            return

        self._following.clear()
        self._following[member] = FollowedMember(member)
        logger.info(
            f'on_voice_move master moved channel -> set_channel '
            f'user_id={member.id} new_channel_id={member.voice.channel.id}'
        )
        await self._speaker.set_channel(member.voice.channel)

    async def go_to_master(self):
        mast = self._master
        logger.debug(
            f'go_to_master guild_id={self._guild.id} master={(format_member_voice(mast, "master") if mast else "<None>")} '
            f'| SNAP {format_guild_voice_snapshot(self._guild)}'
        )
        if mast is not None and mast.voice is not None and mast.voice.channel is not None:
            await self._speaker.set_channel(mast.voice.channel)
        else:
            logger.warning(
                f'go_to_master no-op guild_id={self._guild.id} master_present={mast is not None} '
                f'master_vc={format_discord_voice_state(getattr(mast, "voice", None), "master") if mast else "<None>"} '
                f'| SNAP {format_guild_voice_snapshot(self._guild)}'
            )
