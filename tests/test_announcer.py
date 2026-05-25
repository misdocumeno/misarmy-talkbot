"""Error reply announcer cooldown (prevents spam during repeated TTS failures)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from misarmy_talkbot.core.ops.announcer import ErrorReplyAnnouncer
from tests.helpers import make_discord_message


@pytest.mark.asyncio
async def test_announce_respects_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('OPS_ANNOUNCE_COOLDOWN_SECONDS', '300')
    announcer = ErrorReplyAnnouncer(guild_id=1)
    message = make_discord_message()
    message.reply = AsyncMock()

    await announcer.announce(message, 'tts', 'first')
    await announcer.announce(message, 'tts', 'second')
    assert message.reply.await_count == 1


@pytest.mark.asyncio
async def test_reset_cooldown_allows_immediate_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('OPS_ANNOUNCE_COOLDOWN_SECONDS', '300')
    announcer = ErrorReplyAnnouncer(guild_id=1)
    message = make_discord_message()
    message.reply = AsyncMock()

    await announcer.announce(message, 'tts', 'first')
    announcer.reset_cooldown(message.author.id)
    await announcer.announce(message, 'tts', 'after reset')
    assert message.reply.await_count == 2
