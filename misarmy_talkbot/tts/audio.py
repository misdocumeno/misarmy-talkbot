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
        author = cast(discord.Member, self.original.author)
        self.state = AudioState.PROCESSING
        await self._on_state_change()

        voice_settings = await get_voice(author)
        logger.debug(f'Processing {self.content!r} with {voice_settings}')

        if self.content == '':
            self.state = AudioState.READY
            await self._on_state_change()
            return

        try:
            match voice_settings.voice.split('/')[0]:
                case 'edge':
                    self.buffer = await generate_edge_tts(self.content, author)
                case 'google':
                    self.buffer = await generate_google_tts(self.content, author)
        except NoTokensError:
            pass
        except AssertionError:
            await self.original.reply(embed=discord.Embed(
                description=translate('generate_audio_failed', self.original.guild), color=discord.Colour.red()))
            logger.warning(f'Could not play {self.content!r} with {voice_settings}')

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
