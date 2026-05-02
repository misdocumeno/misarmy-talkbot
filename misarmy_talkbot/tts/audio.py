import io
import time
import asyncio
import discord
from enum import Enum, auto
from typing import cast
from .edits import apply_edits
from ..database.voice import get_voice
from .voices.edge import generate_edge_tts
from .voices.google import generate_google_tts, NoTokensError
from ..locale.translations import translate
from ..logger import logger


class AudioState(Enum):
    PENDING = auto()
    PROCESSING = auto()
    READY = auto()


class AudioMessage:
    content: str
    original: discord.Message
    timestamp: float
    state: AudioState
    buffer: io.BytesIO | None
    _condition: asyncio.Condition

    def __init__(self, message: discord.Message):
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)
        self.buffer = None
        self._condition = asyncio.Condition()
        self._state_change_callback = None

    async def process(self):
        """
        Move PENDING→PROCESSING→READY so _speak can dequeue index 0. Any unexpected error must still reach READY
        or the queue blocks head-of-line forever.
        """
        author = cast(discord.Member, self.original.author)
        self.state = AudioState.PROCESSING
        guild_id = self.original.guild.id if self.original.guild else None
        logger.debug(
            f'AudioMessage PROCESSING start audio_msg_obj_id={id(self)} orig_msg_obj_id={id(self.original)} '
            f'msg_id={self.original.id} guild_id={guild_id} author_obj_id={id(author)} author_id={author.id} '
            f'content_len={len(self.content)} condition_obj_id={id(self._condition)}'
        )
        try:
            await self._on_state_change()

            voice_settings = await get_voice(author)
            logger.debug(
                f'AudioMessage TTS audio_msg_obj_id={id(self)} msg_id={self.original.id} {self.content!r} '
                f'settings={voice_settings}'
            )

            if self.content == '':
                self.state = AudioState.READY
                logger.debug(
                    f'AudioMessage empty content -> READY audio_msg_obj_id={id(self)} msg_id={self.original.id}'
                )
                await self._on_state_change()
                return

            try:
                match voice_settings.voice.split('/')[0]:
                    case 'edge':
                        self.buffer = await generate_edge_tts(self.content, author)
                    case 'google':
                        self.buffer = await generate_google_tts(self.content, author)
            except NoTokensError:
                logger.debug(
                    f'AudioMessage NoTokensError (silent) msg_id={self.original.id} '
                    f'content_preview={self.content[:80]!r}'
                )
            except AssertionError:
                await self.original.reply(embed=discord.Embed(
                    description=translate('generate_audio_failed', self.original.guild), color=discord.Colour.red()))
                logger.warning(f'Could not play {self.content!r} with {voice_settings}')

            self.state = AudioState.READY
            buf_len = len(self.buffer.getvalue()) if self.buffer else 0
            logger.debug(
                f'AudioMessage -> READY audio_msg_obj_id={id(self)} orig_msg_obj_id={id(self.original)} '
                f'msg_id={self.original.id} buffer_bytes={buf_len} content_len={len(self.content)}'
            )
            await self._on_state_change()
        except Exception:
            logger.exception(
                f'AudioMessage process FAILED audio_msg_obj_id={id(self)} orig_msg_obj_id={id(self.original)} '
                f'msg_id={self.original.id} guild_id={guild_id} forcing READY with no buffer so queue cannot deadlock'
            )
            self.buffer = None
            self.state = AudioState.READY
            await self._on_state_change()

    async def edit(self, message: discord.Message):
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)

    async def _on_state_change(self):
        pass

    def __eq__(self, other):
        return (isinstance(other, AudioMessage) and self.original == other.original) or (
            isinstance(other, discord.Message) and self.original == other)

    def __repr__(self):
        return f'AudioMessage(state={self.state}, content={self.content!r})'
