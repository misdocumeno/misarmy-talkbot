"""One Discord message represented as a TTS audio file on tmpfs."""

from __future__ import annotations

import time
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

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
from misarmy_talkbot.infra.audio_storage import AudioStorage
from misarmy_talkbot.infra.database.voice import get_voice
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from pathlib import Path

    import discord


class AudioState(Enum):
    PENDING = auto()
    PROCESSING = auto()
    READY = auto()
    FAILED = auto()


class AudioMessage:
    """Lifecycle for one chat message: text edits -> TTS bytes -> tmpfs file.

    The file path is what the engine hands to Lavalink. The MP3 stays on tmpfs
    until the engine deletes it after playback (or the storage janitor sweeps
    orphans).
    """

    content: str
    original: discord.Message
    timestamp: float
    state: AudioState
    track_path: Path | None
    audio_bytes: int

    def __init__(
        self,
        message: discord.Message,
        *,
        storage: AudioStorage | None = None,
    ) -> None:
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)
        self.track_path = None
        self.audio_bytes = 0
        self._storage = storage or AudioStorage.instance()

    async def process(self) -> None:
        """Generate TTS audio and write it to tmpfs; sets ``state`` accordingly."""
        author = cast('discord.Member', self.original.author)
        self.state = AudioState.PROCESSING

        voice_settings = await get_voice(author)
        logger.debug('tts_processing %r %s', self.content, voice_settings)

        if self.content == '':
            self.state = AudioState.READY
            return

        try:
            provider = voice_settings.voice.split('/')[0]
            match provider:
                case 'edge':
                    edge_voice, rate, pitch = edge_tts_params(voice_settings)
                    buffer = await generate_edge_tts(
                        self.content, voice=edge_voice, rate=rate, pitch=pitch
                    )
                case 'google':
                    g_voice, aresample, asetrate, atempo = google_tts_params(
                        voice_settings
                    )
                    buffer = await generate_google_tts(
                        self.content,
                        voice=g_voice,
                        aresample=aresample,
                        asetrate=asetrate,
                        atempo=atempo,
                    )
                case _:
                    raise ValueError(f'unsupported TTS provider: {provider!r}')
        except NoTokensError:
            logger.warning(
                'tts_no_tokens content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            self.state = AudioState.FAILED
            return
        except AssertionError:
            logger.warning(
                'tts_assertion content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            self.state = AudioState.FAILED
            return

        payload = buffer.getvalue()
        if not payload:
            logger.warning(
                'tts_empty_audio content=%r voice=%s',
                self.content,
                voice_settings.voice,
            )
            self.state = AudioState.FAILED
            return

        self.track_path = await self._storage.write(payload)
        self.audio_bytes = len(payload)
        self.state = AudioState.READY
        logger.debug(
            'tts_ready content=%r bytes=%s path=%s',
            self.content,
            self.audio_bytes,
            self.track_path,
        )

    async def edit(self, message: discord.Message) -> None:
        """Reset to PENDING with new content; previously-written file is dropped."""
        await self._storage.delete(self.track_path)
        self.track_path = None
        self.audio_bytes = 0
        self.state = AudioState.PENDING
        self.original = message
        self.timestamp = time.time()
        self.content = apply_edits(message)

    async def cleanup(self) -> None:
        """Remove the on-disk MP3 (idempotent)."""
        if self.track_path is not None:
            await self._storage.delete(self.track_path)
            self.track_path = None

    def __eq__(self, other: object) -> bool:
        from discord import Message  # local import to keep boot-order simple

        return (
            isinstance(other, AudioMessage) and self.original == other.original
        ) or (isinstance(other, Message) and self.original == other)

    def __hash__(self) -> int:
        return hash(self.original.id)

    def __repr__(self) -> str:
        return (
            f'AudioMessage(state={self.state.name}, content={self.content!r})'
        )
