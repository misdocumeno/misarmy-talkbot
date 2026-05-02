import io
import asyncio
import discord
from typing import cast
from .audio import AudioMessage, AudioState
from .queue import MessageQueue
from ..logger import logger
from ..voice_log import format_guild_voice_snapshot


def _asyncio_task_audit(speaker_id: int, phase: str) -> str:
    """Identify the running coroutine (multiple _speak loops are the main ghost hypothesis)."""
    ct = asyncio.current_task()
    if ct is None:
        return f'{phase} speaker_id={speaker_id} current_task=None'
    return (
        f'{phase} speaker_id={speaker_id} coro_task_name={ct.get_name()!r} asyncio_task_id={id(ct)} '
        f'done={ct.done()} cancelled={ct.cancelled()}'
    )


class GuildSpeaker:
    _guild: discord.Guild
    _voice_state_lock: asyncio.Lock
    _queue: MessageQueue
    _playing: AudioMessage | None

    def __init__(self, guild: discord.Guild):
        self._guild = guild
        self._voice_state_lock = asyncio.Lock()
        self._queue = MessageQueue()
        self._playing = None
        self._speak_task = asyncio.create_task(self._speak(), name=f'speak-guild-{guild.id}')
        logger.debug(
            f'GuildSpeaker init guild_snowflake_id={guild.id} guild_obj_id={id(guild)} speaker_obj_id={id(self)} '
            f'queue_obj_id={id(self._queue)} voice_state_lock_obj_id={id(self._voice_state_lock)} '
            f'speak_asyncio_task_id={id(self._speak_task)} speak_task_name={self._speak_task.get_name()!r}'
        )

    def _voice_debug(self, label: str) -> str:
        qn = len(self._queue)
        playing = self._playing is not None
        vc = cast(discord.VoiceClient | None, self._guild.voice_client)
        playing_oid = id(self._playing) if self._playing is not None else None
        return (
            f'[{label}] speaker_obj_id={id(self)} guild_obj_id={id(self._guild)} '
            f'queue_items={qn} actively_playing={playing} _playing_audio_msg_obj_id={playing_oid} '
            f'voice_state_lock_obj_id={id(self._voice_state_lock)} speak_task_asyncio_id={id(self._speak_task)} '
            f'voice_client_obj_id={id(vc) if vc else None} | {self._queue.debug_items_identity_raw()} '
            f'| {format_guild_voice_snapshot(self._guild)}'
        )

    async def set_channel(self, channel: discord.VoiceChannel | discord.StageChannel | None):
        ch_id = channel.id if channel else None
        logger.debug(
            f'set_channel requested target_channel_id={ch_id} | {self._voice_debug("before_lock")}'
        )
        async with self._voice_state_lock:
            logger.debug(
                f'set_channel lock acquired speaker_id={id(self)} guild_id={self._guild.id} | '
                f'{_asyncio_task_audit(id(self), "set_channel")}'
            )
            voice_client = cast(discord.VoiceClient | None, self._guild.voice_client)

            if self._guild.voice_client is not None and voice_client and voice_client.is_playing():
                logger.debug('set_channel stopping current playback before voice state change')
                voice_client.stop()

            if channel is not None and self._guild.voice_client is None:
                logger.info(f'set_channel connecting to voice channel_id={ch_id} reconnect=True')
                await channel.connect(reconnect=True)
                await self._guild.change_voice_state(channel=channel, self_deaf=True)
                logger.debug(f'set_channel connected | {self._voice_debug("after_connect")}')
            elif channel is None and self._guild.voice_client is not None:
                logger.info('set_channel disconnecting voice client and clearing queue')
                await self._guild.voice_client.disconnect(force=True)
                await self._queue.clear()
                logger.debug(f'set_channel disconnected | {self._voice_debug("after_disconnect")}')
            else:
                logger.debug(
                    f'set_channel change_voice_state only (no connect/disconnect) target_channel_id={ch_id} '
                    f'| {self._voice_debug("else_branch")}'
                )
                await self._guild.change_voice_state(channel=channel, self_deaf=True)
                logger.debug(f'set_channel change_voice_state done | {self._voice_debug("after_change_state")}')

    async def on_message(self, message: discord.Message):
        me_v = self._guild.me.voice
        if me_v is None or me_v.channel is None:
            logger.warning(
                f'speaker SKIP (no_bot_voice) msg_obj_id={id(message)} msg_id={message.id} '
                f'author_obj_id={id(message.author)} author_id={message.author.id} '
                f'guild_id={self._guild.id} | {self._voice_debug("on_message_skip")}'
            )
            return
        logger.info(
            f'speaker ACCEPT enqueue msg_obj_id={id(message)} msg_id={message.id} '
            f'ch_obj_id={id(message.channel)} author_obj_id={id(message.author)} author_id={message.author.id} '
            f'guild_id={self._guild.id} bot_ch_id={me_v.channel.id}'
        )
        logger.debug(f'speaker ACCEPT detail | {self._voice_debug("on_message_ok")}')
        audio = AudioMessage(message)
        await self._queue.put(audio)
        await audio.process()

    async def on_message_edit(self, message: discord.Message):
        for queued in self._queue:
            if queued.original == message:
                logger.debug(f'on_message_edit re-queue msg_id={message.id}')
                await queued.edit(message)
                await queued.process()
                return
        logger.debug(f'on_message_edit no queued match msg_id={message.id}')

    async def on_message_delete(self, message: discord.Message):
        if message in self._queue:
            logger.debug(f'on_message_delete remove msg_id={message.id}')
            await self._queue.remove(message)
        else:
            logger.debug(f'on_message_delete no-op msg_id={message.id}')

    async def _speak(self):
        while True:
            try:
                message = await self._queue.get(AudioState.READY, index=0)
                me_v = self._guild.me.voice
                vc = self._guild.voice_client
                vc_playing = bool(
                    vc and getattr(vc, 'is_playing', lambda: False)()
                )
                if vc_playing:
                    logger.warning(
                        f'_speak VoiceClient.is_playing already True BEFORE this speaker plays '
                        f'(strong hint: another _speak/_play coroutine on same guild VC) '
                        f'{_asyncio_task_audit(id(self), "_speak")} voice_client_id={id(vc) if vc else None} '
                        f'audio_msg_obj_id={id(message)} msg_id={message.original.id}'
                    )
                logger.debug(
                    f'_speak dequeued audio_msg_obj_id={id(message)} msg_id={message.original.id} state={message.state} '
                    f'buffer_set={message.buffer is not None} content_len={len(message.content)} '
                    f'bot_chan_id={(me_v.channel.id if me_v and me_v.channel else None)} '
                    f'vc_connected={getattr(vc, "is_connected", lambda: False)() if vc else None} '
                    f'vc_is_playing_pre={vc_playing} | {_asyncio_task_audit(id(self), "_speak")} | '
                    f'{self._voice_debug("_speak_after_dequeue")}'
                )
                if message.buffer is None and message.content != '':
                    logger.debug(
                        f'_speak skipping play (no buffer) audio_msg_obj_id={id(message)} msg_id={message.original.id} '
                        f'likely TTS failure or empty pipeline'
                    )
                elif me_v is None or me_v.channel is None:
                    logger.warning(
                        f'_speak dropping ready audio (no bot voice) audio_msg_obj_id={id(message)} '
                        f'msg_id={message.original.id} | {self._voice_debug("_speak_drop_no_voice")}'
                    )
                elif message.buffer is not None:
                    logger.debug(
                        f'_play invoke audio_msg_obj_id={id(message)} msg_id={message.original.id} '
                        f'content={message.content!r} | {_asyncio_task_audit(id(self), "_speak_invokes_play")}'
                    )
                    await self._play(message)
                    logger.debug(
                        f'_play finished audio_msg_obj_id={id(message)} msg_id={message.original.id} | '
                        f'{_asyncio_task_audit(id(self), "_speak_after_play")}'
                    )
            except Exception:
                logger.exception('_speak loop: unhandled exception (task continues)')

    async def _play(self, message: AudioMessage):
        self._playing = message
        voice_client = cast(discord.VoiceClient | None, self._guild.voice_client)
        if voice_client is None:
            logger.error(
                f'_play aborted: voice_client is None audio_msg_obj_id={id(message)} msg_id={message.original.id} '
                f'| {self._voice_debug("_play")}'
            )
            self._playing = None
            return
        already = voice_client.is_playing()
        if already:
            logger.warning(
                f'_play ENTRY VoiceClient already is_playing=True — overlapping play() risk '
                f'{_asyncio_task_audit(id(self), "_play")} audio_msg_obj_id={id(message)} '
                f'msg_id={message.original.id} voice_client_id={id(voice_client)}'
            )
        logger.debug(
            f'_play FFmpeg start audio_msg_obj_id={id(message)} msg_id={message.original.id} '
            f'vc_connected={voice_client.is_connected()} '
            f'vc_channel={(voice_client.channel.id if voice_client.channel else None)} '
            f'vc_was_playing_before_play={already} voice_client_id={id(voice_client)} | '
            f'{_asyncio_task_audit(id(self), "_play")}'
        )
        source = discord.FFmpegPCMAudio(cast(io.BytesIO, message.buffer), pipe=True)
        voice_client.play(source)
        started = False
        for _ in range(10):
            await asyncio.sleep(0.05)
            if voice_client.is_playing():
                started = True
                break
        if not started:
            logger.warning(
                f'_play voice never entered is_playing() audio_msg_obj_id={id(message)} msg_id={message.original.id} '
                f'(ffmpeg disconnect or transport issue?) | {self._voice_debug("_play_no_audio")}'
            )
        # something weird happens and idk why. if we use the after callback for voice_client.play
        # it doesn't always get called in time, for some reason. but if we constantly check is_playing,
        # it gets called when the audio finishes, without any extra delay. so we use that instead.
        while voice_client.is_playing():
            await asyncio.sleep(0.1)
        self._playing = None

    async def stop(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            logger.debug(f'stop member_id={member.id} nothing playing')
            return discord.Colour.red(), 'not_talking'

        member_messages = [message for message in self._queue if message.original.author == member]
        logger.debug(
            f'stop member_id={member.id} queued_hits={len(member_messages)} '
            f'playing_match={self._playing.original.author == member if self._playing else False}'
        )

        for message in member_messages:
            await self._queue.remove(message)

        if self._playing is not None and self._playing.original.author == member:
            vc = cast(discord.VoiceClient | None, self._guild.voice_client)
            if vc:
                vc.stop()
            else:
                logger.warning(f'stop wanted voice_client.stop() but voice_client is None member_id={member.id}')

        return discord.Colour.dark_purple(), 'shut_up_success'

    async def stop_all(self) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            logger.debug('stop_all nothing playing')
            return discord.Colour.red(), 'not_talking'
        logger.info(f'stop_all clearing queue len_was={len(self._queue)}')
        await self._queue.clear()
        vc = cast(discord.VoiceClient | None, self._guild.voice_client)
        if vc:
            vc.stop()
        else:
            logger.warning('stop_all voice_client is None')
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def skip(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            logger.debug(f'skip member_id={member.id} nothing playing')
            return discord.Colour.red(), 'not_talking'

        current = self._playing or self._queue[0]

        if current.original.author != member and not member.guild_permissions.mute_members:
            logger.debug(
                f'skip denied member_id={member.id} current_author_id={current.original.author.id} '
                f'has_mute_perm={member.guild_permissions.mute_members}'
            )
            return discord.Colour.red(), 'skip_no_permission'

        logger.debug(
            f'skip granted member_id={member.id} stopping_current={self._playing is not None} '
            f'current_msg_id={current.original.id}'
        )
        if self._playing is not None:
            vc = cast(discord.VoiceClient | None, self._guild.voice_client)
            if vc:
                vc.stop()
            else:
                logger.warning('skip voice_client is None while _playing set')
        else:
            await self._queue.remove(current)

        return discord.Colour.dark_purple(), 'shut_up_success'
