"""Forward / snapshot text extraction for TTS."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import discord

from misarmy_talkbot.core.playback.tts.edits import apply_edits, raw_tts_text


def _message(
    *,
    content: str = '',
    snapshots: list[object] | None = None,
    forwarded: bool = False,
) -> discord.Message:
    guild = MagicMock(spec=discord.Guild)
    guild.id = 1
    message = MagicMock(spec=discord.Message)
    message.id = 42
    message.guild = guild
    message.content = content
    message.message_snapshots = snapshots or []
    message.stickers = []
    message.mentions = []
    message.role_mentions = []
    message.channel_mentions = []
    flags = MagicMock()
    flags.forwarded = forwarded
    message.flags = flags
    return message


def test_raw_tts_text_uses_snapshot_when_content_empty() -> None:
    snap = MagicMock()
    snap.content = 'hola desde forward'
    message = _message(content='', snapshots=[snap], forwarded=True)
    assert raw_tts_text(message) == 'hola desde forward'


def test_raw_tts_text_prefers_message_content() -> None:
    snap = MagicMock()
    snap.content = 'snapshot'
    message = _message(content='directo', snapshots=[snap])
    assert raw_tts_text(message) == 'directo'


def test_apply_edits_reads_forward_snapshot() -> None:
    snap = MagicMock()
    snap.content = 'Texto Forwarded'
    message = _message(content='', snapshots=[snap], forwarded=True)
    empty = MagicMock(
        emojis={},
        stickers={},
        mentions={},
        roles={},
        channels={},
        regex={},
    )
    guild_cfg = MagicMock(replacements=empty)
    with (
        patch(
            'misarmy_talkbot.core.playback.tts.edits.global_config',
            MagicMock(replacements=empty),
        ),
        patch(
            'misarmy_talkbot.core.playback.tts.edits.config',
            {message.guild: guild_cfg},
        ),
    ):
        assert apply_edits(message) == 'texto forwarded'
