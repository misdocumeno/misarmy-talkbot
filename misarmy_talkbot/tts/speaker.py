import io
import asyncio
import discord
from typing import cast
from .audio import AudioMessage, AudioState
from .queue import MessageQueue
from ..logger import logger


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
        asyncio.create_task(self._speak())

    async def set_channel(self, channel: discord.VoiceChannel | discord.StageChannel | None):
        async with self._voice_state_lock:
            voice_client = cast(discord.VoiceClient, self._guild.voice_client)

            if self._guild.voice_client is not None and voice_client.is_playing():
                voice_client.stop()

            if channel is not None and self._guild.voice_client is None:
                await channel.connect(reconnect=True)
                await self._guild.change_voice_state(channel=channel, self_deaf=True)
            elif channel is None and self._guild.voice_client is not None:
                await self._guild.voice_client.disconnect(force=True)
                await self._queue.clear()
            else:
                await self._guild.change_voice_state(channel=channel, self_deaf=True)

    async def on_message(self, message: discord.Message):
        if self._guild.me.voice is not None and self._guild.me.voice.channel is not None:
            audio = AudioMessage(message)
            await self._queue.put(audio)
            await audio.process()

    async def on_message_edit(self, message: discord.Message):
        for queued in self._queue:
            if queued.original == message:
                await queued.edit(message)
                await queued.process()
                break

    async def on_message_delete(self, message: discord.Message):
        if message in self._queue:
            await self._queue.remove(message)

    async def _speak(self):
        while True:
            try:
                message = await self._queue.get(AudioState.READY, index=0)
                if (
                    message.buffer is not None
                    and self._guild.me.voice is not None
                    and self._guild.me.voice.channel is not None
                ):
                    logger.debug(f'Playing {message.content!r}')
                    await self._play(message)
            except Exception as e:
                logger.error('Unhanded exception:')
                logger.error(e)

    async def _play(self, message: AudioMessage):
        self._playing = message
        voice_client = cast(discord.VoiceClient, self._guild.voice_client)
        voice_client.play(discord.FFmpegPCMAudio(cast(io.BytesIO, message.buffer), pipe=True))
        # something weird happens and idk why. if we use the after callback for voice_client.play
        # it doesn't always get called in time, for some reason. but if we constantly check is_playing,
        # it gets called when the audio finishes, without any extra delay. so we use that instead.
        while voice_client.is_playing():
            await asyncio.sleep(0.1)
        self._playing = None

    async def stop(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'

        member_messages = [message for message in self._queue if message.original.author == member]

        for message in member_messages:
            await self._queue.remove(message)

        if self._playing is not None and self._playing.original.author == member:
            cast(discord.VoiceClient, self._guild.voice_client).stop()

        return discord.Colour.dark_purple(), 'shut_up_success'

    async def stop_all(self) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'
        await self._queue.clear()
        cast(discord.VoiceClient, self._guild.voice_client).stop()
        return discord.Colour.dark_purple(), 'shut_up_success'

    async def skip(self, member: discord.Member) -> tuple[discord.Colour, str]:
        if self._playing is None and len(self._queue) == 0:
            return discord.Colour.red(), 'not_talking'

        current = self._playing or self._queue[0]

        if current.original.author != member and not member.guild_permissions.mute_members:
            return discord.Colour.red(), 'skip_no_permission'

        if self._playing is not None:
            cast(discord.VoiceClient, self._guild.voice_client).stop()
        else:
            await self._queue.remove(current)

        return discord.Colour.dark_purple(), 'shut_up_success'
