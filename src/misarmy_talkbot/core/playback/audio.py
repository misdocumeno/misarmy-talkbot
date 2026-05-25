import asyncio
import io
import time
from enum import Enum, auto
from typing import cast

import discord

from misarmy_talkbot.core.playback.tts.edits import apply_edits
from misarmy_talkbot.core.playback.tts.voice_settings import (
    edge_tts_params,
    google_tts_params,
)
from misarmy_talkbot.core.playback.tts.voices.edge import generate_edge_tts
from misarmy_talkbot.core.playback.tts.voices.google import (
    NoTokensError,
    generate_google_tts,
)
from misarmy_talkbot.infra.database.voice import get_voice
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.trace import step


class AudioState(Enum):
    PENDING = auto()
    PROCESSING = auto()
    READY = auto()
    FAILED = auto()


class AudioMessage:
    content: str
    original: discord.Message
    timestamp: float
    state: AudioState
    buffer: io.BytesIO | None
    _condition: asyncio.Condition

    def __init__(self, message: discord.Message) -> None:
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)
        self.buffer = None
        self.play_attempts = 0
        self._condition = asyncio.Condition()
        self._state_change_callback = None

    async def process(self) -> None:
        author = cast('discord.Member', self.original.author)
        guild_id = self.original.guild.id if self.original.guild else None
        step(guild_id, 'audio', 'process', 'ENTER', content=self.content)
        self.state = AudioState.PROCESSING
        await self._on_state_change()

        voice_settings = await get_voice(author)
        logger.debug(f'Processing {self.content!r} with {voice_settings}')

        if self.content == '':
            self.state = AudioState.READY
            await self._on_state_change()
            step(
                guild_id,
                'audio',
                'process',
                'EXIT',
                content=self.content,
                state='READY',
                reason='empty',
            )
            return

        try:
            step(
                guild_id,
                'audio',
                'tts_generate',
                'ENTER',
                content=self.content,
                voice=voice_settings.voice,
            )
            provider = voice_settings.voice.split('/')[0]
            match provider:
                case 'edge':
                    edge_voice, rate, pitch = edge_tts_params(voice_settings)
                    self.buffer = await generate_edge_tts(
                        self.content, voice=edge_voice, rate=rate, pitch=pitch
                    )
                case 'google':
                    g_voice, aresample, asetrate, atempo = google_tts_params(
                        voice_settings
                    )
                    self.buffer = await generate_google_tts(
                        self.content,
                        voice=g_voice,
                        aresample=aresample,
                        asetrate=asetrate,
                        atempo=atempo,
                    )
                case _:
                    raise ValueError(f'unsupported TTS provider: {provider!r}')
            step(
                guild_id,
                'audio',
                'tts_generate',
                'EXIT',
                content=self.content,
                bytes=self.buffer.getbuffer().nbytes if self.buffer else 0,
            )
        except NoTokensError:
            logger.warning(
                'No TTS tokens for %r with %s', self.content, voice_settings
            )
            self.state = AudioState.FAILED
            await self._on_state_change()
            step(
                guild_id,
                'audio',
                'process',
                'EXIT',
                content=self.content,
                state='FAILED',
                reason='no_tokens',
            )
            return
        except AssertionError:
            logger.warning(
                'Could not play %r with %s', self.content, voice_settings
            )
            self.state = AudioState.FAILED
            await self._on_state_change()
            step(
                guild_id,
                'audio',
                'process',
                'EXIT',
                content=self.content,
                state='FAILED',
                reason='assertion',
            )
            return

        size = self.buffer.getbuffer().nbytes
        if size == 0:
            logger.warning(
                'tts_empty_audio content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            self.state = AudioState.FAILED
            await self._on_state_change()
            step(
                guild_id,
                'audio',
                'process',
                'EXIT',
                content=self.content,
                state='FAILED',
                reason='empty_audio',
            )
            return

        self.state = AudioState.READY
        await self._on_state_change()
        logger.debug('tts_ready content=%r bytes=%s', self.content, size)
        step(
            guild_id,
            'audio',
            'process',
            'EXIT',
            content=self.content,
            state='READY',
            bytes=size,
        )

    async def edit(self, message: discord.Message) -> None:
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)

    async def _on_state_change(self) -> None:
        pass

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, AudioMessage) and self.original == other.original
        ) or (isinstance(other, discord.Message) and self.original == other)

    def __repr__(self) -> str:
        return f'AudioMessage(state={self.state}, content={self.content!r})'
