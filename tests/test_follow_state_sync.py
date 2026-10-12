"""Follow state vs. real voice state when things leave the happy path.

Each test sets up the registry the way a real incident leaves it (bot kicked
from voice, master removed from the guild, master moved where the bot cannot
follow) and checks that follows and the guild session end up matching Discord.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, PropertyMock, patch

import discord
import pytest

from misarmy_talkbot.app.events import register_events
from misarmy_talkbot.app.follow_ops import follow_user, message_for_followed
from misarmy_talkbot.app.voice_sync import reconcile_master_channel
from misarmy_talkbot.core.follow.registry import FollowRegistry
from misarmy_talkbot.core.playback.engine import PlaybackEngine
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.core.voice.lavalink_session import LavalinkSession
from tests.helpers import make_discord_message, noop_async

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

GUILD_ID = 1
BOT_ID = 500
MASTER_ID = 10
OTHER_ID = 20


def _voice_state(channel_id: int | None) -> discord.VoiceState:
    state = MagicMock(spec=discord.VoiceState)
    if channel_id is None:
        state.channel = None
    else:
        state.channel = MagicMock(spec=discord.VoiceChannel)
        state.channel.id = channel_id
        state.channel.__eq__ = (
            lambda _self, other: getattr(other, 'id', None) == channel_id
        )
    return state


class FakeBot:
    """Collects ``@bot.event`` handlers and serves one guild."""

    def __init__(self) -> None:
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.user = MagicMock(spec=discord.ClientUser)
        self.user.id = BOT_ID
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.id = GUILD_ID
        self.members: dict[int, discord.Member] = {}
        self.guild.get_member = self.members.get

    def event(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        self.handlers[handler.__name__] = handler
        return handler

    def get_guild(self, guild_id: int) -> discord.Guild | None:
        return self.guild if guild_id == GUILD_ID else None

    def add_member(self, user_id: int, channel_id: int | None) -> None:
        member = MagicMock(spec=discord.Member)
        member.id = user_id
        member.guild = self.guild
        member.voice = None if channel_id is None else _voice_state(channel_id)
        self.members[user_id] = member


@pytest.fixture
def bot() -> Generator[FakeBot, None, None]:
    fake = FakeBot()
    GuildSessionRegistry.instance().bind_bot(fake)  # type: ignore[arg-type]
    with (
        patch.object(PlaybackEngine, 'start', lambda _self: None),
        patch.object(PlaybackEngine, 'shutdown', noop_async),
        patch.object(LavalinkSession, 'disconnect', noop_async),
    ):
        yield fake


def _bot_member(bot: FakeBot) -> discord.Member:
    member = MagicMock(spec=discord.Member)
    member.id = BOT_ID
    member.guild = bot.guild
    member.__eq__ = lambda _self, other: getattr(other, 'id', None) == BOT_ID
    return member


async def _follow_master(bot: FakeBot) -> None:
    follow_registry = FollowRegistry.instance()
    follow_registry.follow(GUILD_ID, MASTER_ID)
    follow_registry.set_master(GUILD_ID, MASTER_ID)
    bot.add_member(MASTER_ID, 100)
    await GuildSessionRegistry.instance().get_or_create(GUILD_ID)


@pytest.mark.asyncio
async def test_bot_kicked_from_voice_clears_follows_and_session(
    bot: FakeBot,
) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)

    await bot.handlers['on_voice_state_update'](
        _bot_member(bot), _voice_state(100), _voice_state(None)
    )

    assert not FollowRegistry.instance().is_tracked(GUILD_ID)
    assert GuildSessionRegistry.instance().get(GUILD_ID) is None


@pytest.mark.asyncio
async def test_bot_moved_by_someone_clears_follows_and_session(
    bot: FakeBot,
) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)

    await bot.handlers['on_voice_state_update'](
        _bot_member(bot), _voice_state(100), _voice_state(200)
    )

    assert not FollowRegistry.instance().is_tracked(GUILD_ID)
    assert GuildSessionRegistry.instance().get(GUILD_ID) is None


@pytest.mark.asyncio
async def test_bot_move_we_requested_keeps_follows(bot: FakeBot) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)
    guild_session = GuildSessionRegistry.instance().get(GUILD_ID)
    assert guild_session is not None
    guild_session.lavalink._requested_moves.add(200)

    await bot.handlers['on_voice_state_update'](
        _bot_member(bot), _voice_state(100), _voice_state(200)
    )

    assert FollowRegistry.instance().master(GUILD_ID) == MASTER_ID
    assert GuildSessionRegistry.instance().get(GUILD_ID) is guild_session
    assert not guild_session.lavalink._requested_moves


@pytest.mark.asyncio
async def test_message_is_only_spoken_in_its_own_guild(bot: FakeBot) -> None:
    follow_registry = FollowRegistry.instance()
    for guild_id in (GUILD_ID, 2):
        follow_registry.follow(guild_id, MASTER_ID)
        follow_registry.set_master(guild_id, MASTER_ID)
    enqueued: list[int] = []

    async def capture(self: PlaybackEngine, _audio: object) -> None:
        enqueued.append(self.guild_id)

    with patch.object(PlaybackEngine, 'enqueue', capture):
        for guild_id in (GUILD_ID, 2):
            await GuildSessionRegistry.instance().get_or_create(guild_id)
        message = make_discord_message(guild_id=2, author_id=MASTER_ID)
        with patch(
            'misarmy_talkbot.app.follow_ops.AudioMessage',
            lambda msg: msg,
        ):
            await message_for_followed(bot, message)  # type: ignore[arg-type]

    assert enqueued == [2]


@pytest.mark.asyncio
async def test_removed_master_is_replaced_without_waiting_for_grace(
    bot: FakeBot,
) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)
    FollowRegistry.instance().follow(GUILD_ID, OTHER_ID)
    bot.add_member(OTHER_ID, 100)
    payload = MagicMock(spec=discord.RawMemberRemoveEvent)
    payload.guild_id = GUILD_ID
    payload.user = MagicMock(id=MASTER_ID)

    with patch.object(
        LavalinkSession, 'ensure_connected_to', return_value=None
    ):
        await bot.handlers['on_raw_member_remove'](payload)

    assert FollowRegistry.instance().master(GUILD_ID) == OTHER_ID
    assert not FollowRegistry.instance().is_followed(GUILD_ID, MASTER_ID)


@pytest.mark.asyncio
async def test_removed_last_follower_disposes_session(bot: FakeBot) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)
    payload = MagicMock(spec=discord.RawMemberRemoveEvent)
    payload.guild_id = GUILD_ID
    payload.user = MagicMock(id=MASTER_ID)

    await bot.handlers['on_raw_member_remove'](payload)

    assert not FollowRegistry.instance().is_tracked(GUILD_ID)
    assert GuildSessionRegistry.instance().get(GUILD_ID) is None


@pytest.mark.asyncio
async def test_unreachable_last_master_disposes_session(bot: FakeBot) -> None:
    await _follow_master(bot)

    with patch.object(
        LavalinkSession,
        'ensure_connected_to',
        return_value=PermissionError('missing voice perms'),
    ):
        await reconcile_master_channel(bot, GUILD_ID)  # type: ignore[arg-type]

    assert not FollowRegistry.instance().is_tracked(GUILD_ID)
    assert GuildSessionRegistry.instance().get(GUILD_ID) is None


@pytest.mark.asyncio
async def test_rejoining_another_channel_counts_as_a_move(
    bot: FakeBot,
) -> None:
    register_events(bot)  # type: ignore[arg-type]
    await _follow_master(bot)
    FollowRegistry.instance().follow(GUILD_ID, OTHER_ID)
    bot.add_member(OTHER_ID, 200)
    player = MagicMock()
    player.channel.id = 100

    with (
        patch.object(
            LavalinkSession, 'ensure_connected_to', return_value=None
        ),
        patch.object(
            LavalinkSession, 'player', new_callable=PropertyMock
        ) as player_prop,
    ):
        player_prop.return_value = player
        await bot.handlers['on_voice_state_update'](
            bot.members[OTHER_ID], _voice_state(None), _voice_state(200)
        )

    assert not FollowRegistry.instance().is_followed(GUILD_ID, OTHER_ID)
    assert FollowRegistry.instance().master(GUILD_ID) == MASTER_ID


@pytest.mark.asyncio
async def test_follow_with_leftover_session_becomes_master(
    bot: FakeBot,
) -> None:
    await GuildSessionRegistry.instance().get_or_create(GUILD_ID)
    bot.add_member(OTHER_ID, 200)

    with (
        patch.object(
            LavalinkSession, 'ensure_connected_to', return_value=None
        ),
        patch.object(
            LavalinkSession, 'player', new_callable=PropertyMock
        ) as player_prop,
    ):
        player_prop.return_value = MagicMock(channel=MagicMock(id=100))
        result = await follow_user(
            bot,  # type: ignore[arg-type]
            bot.guild,
            bot.members[OTHER_ID],
        )

    assert result[1] == 'follow_success'
    assert FollowRegistry.instance().master(GUILD_ID) == OTHER_ID
