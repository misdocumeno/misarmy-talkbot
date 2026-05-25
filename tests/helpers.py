"""Shared fakes and singleton resets for component tests (no live Discord/Lavalink)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import discord

from misarmy_talkbot.core.follow.grace import DisconnectSupervisor
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.audio import AudioMessage, AudioState
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.audio_storage import AudioStorage
from misarmy_talkbot.observability.metrics import MetricsRegistry


def reset_singletons() -> None:
    FollowRegistry._instance = None
    DisconnectSupervisor._instance = None
    GuildSessionRegistry._instance = None
    MetricsRegistry._instance = None
    AudioStorage._instance = None


def install_temp_audio_storage() -> AudioStorage:
    """Replace the AudioStorage singleton with a tempdir-backed one."""
    tmp = Path(tempfile.mkdtemp(prefix='ttsbot-test-'))
    storage = AudioStorage(audio_dir=tmp)
    AudioStorage._instance = storage
    return storage


class FakeLavalinkPlayer:
    """Minimal wavelink.Player surface used by the engine tests."""

    def __init__(self) -> None:
        self.play_calls: list[object] = []
        self.skip_calls = 0
        self.disconnected = False
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = 9001
        self.channel = channel

    async def play(self, track: object, *, add_history: bool = False) -> None:
        _ = add_history
        self.play_calls.append(track)

    async def skip(self, *, force: bool = False) -> None:
        _ = force
        self.skip_calls += 1

    async def disconnect(self, *, force: bool = False) -> None:
        _ = force
        self.disconnected = True

    async def move_to(self, _channel: object) -> None:
        return None


class FakeLavalinkSession:
    """Stand-in for the real LavalinkSession with no network I/O."""

    def __init__(self, guild_id: int = 7) -> None:
        self.guild_id = guild_id
        self.player = FakeLavalinkPlayer()
        self.connected_to: int | None = None

    async def ensure_connected_to(
        self, channel_id: int
    ) -> None | PermissionError:
        self.connected_to = channel_id
        return None

    async def disconnect(self) -> None:
        await self.player.disconnect(force=True)

    async def play_track(self, player: FakeLavalinkPlayer, track: object) -> None:
        await player.play(track, add_history=False)


class FakeBot:
    def __init__(self, guild_id: int = 1) -> None:
        self.guild_id = guild_id
        guild = MagicMock(spec=discord.Guild)
        guild.id = guild_id
        guild.me = MagicMock()
        guild.voice_client = None
        self._guild = guild

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


def ready_audio_message(
    content: str = 'test', track_path: Path | None = None
) -> AudioMessage:
    """AudioMessage that skips TTS/database (already READY with a track path)."""
    message = make_discord_message(content=content)
    storage = AudioStorage._instance or install_temp_audio_storage()
    with patch(
        'misarmy_talkbot.core.playback.audio.apply_edits', return_value=content
    ):
        audio = AudioMessage(message, storage=storage)
    audio.state = AudioState.READY
    if track_path is None:
        track_path = storage.directory / f'{message.id}.mp3'
        track_path.write_bytes(b'\x00' * 16)
    audio.track_path = track_path
    audio.audio_bytes = track_path.stat().st_size
    return audio


async def noop_async(*_args: Any, **_kwargs: Any) -> None:
    pass
