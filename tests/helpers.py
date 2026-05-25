"""Shared fakes and singleton resets for component tests (no live Discord)."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Callable

import discord

from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioMessage, AudioState
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.metrics import MetricsRegistry


def reset_singletons() -> None:
    FollowRegistry._instance = None
    DisconnectSupervisor._instance = None
    GuildSessionRegistry._instance = None
    MetricsRegistry._instance = None


class FakeVoiceClient:
    """Minimal voice client surface used by pilot and playback tests."""

    def __init__(
        self, *, connected: bool = True, playing: bool = False
    ) -> None:
        self._connected = connected
        self._playing = playing
        self.play_count = 0
        self.stop_count = 0
        self.channel = MagicMock()
        self.channel.id = 9001

    def is_connected(self) -> bool:
        return self._connected

    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        _source: object,
        *,
        after: Callable[[Exception | None], None] | None = None,
    ) -> None:
        self.play_count += 1
        self._playing = True
        if after is not None:
            after(None)

    def stop(self) -> None:
        self.stop_count += 1
        self._playing = False

    async def disconnect(self, *, _force: bool = False) -> None:
        self._connected = False

    async def move_to(self, channel: object) -> None:
        self.channel = channel


class FakeBot:
    def __init__(self, guild_id: int = 1) -> None:
        self.guild_id = guild_id
        self._guild = MagicMock(spec=discord.Guild)
        self._guild.id = guild_id
        self._guild.me = MagicMock()
        self._guild.voice_client = None

    def get_guild(self, guild_id: int) -> discord.Guild | None:
        if guild_id == self.guild_id:
            return self._guild
        return None


def make_discord_message(
    *,
    guild_id: int = 1,
    channel_id: int = 100,
    author_id: int = 42,
    message_id: int = 999,
    content: str = 'hello',
) -> discord.Message:
    guild = MagicMock(spec=discord.Guild)
    guild.id = guild_id
    guild.me = MagicMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.permissions_for = MagicMock(
        return_value=MagicMock(send_messages=True)
    )
    author = MagicMock(spec=discord.Member)
    author.id = author_id
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.guild = guild
    message.channel = channel
    message.author = author
    message.content = content
    return message


def ready_audio_message(content: str = 'test') -> AudioMessage:
    """AudioMessage that skips TTS/database (already READY with a tiny buffer)."""
    message = make_discord_message(content=content)
    with patch(
        'misarmy_talkbot.core.playback.audio.apply_edits', return_value=content
    ):
        audio = AudioMessage(message)
    audio.state = AudioState.READY
    audio.buffer = io.BytesIO(b'\x00' * 8000)
    return audio


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    pass
