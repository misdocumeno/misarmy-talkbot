"""Access control for ``/tts-usage``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.app.tts_usage_access import can_run_tts_usage

if TYPE_CHECKING:
    import pytest


def test_deny_when_allowlists_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('TTS_USAGE_ALLOWED_USER_IDS', raising=False)
    monkeypatch.delenv('TTS_USAGE_ALLOWED_GUILD_IDS', raising=False)
    assert not can_run_tts_usage(user_id=1, guild_id=2)


def test_user_and_guild_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('TTS_USAGE_ALLOWED_USER_IDS', '354114876526034944')
    monkeypatch.setenv('TTS_USAGE_ALLOWED_GUILD_IDS', '814851486621106198')
    assert can_run_tts_usage(
        user_id=354114876526034944, guild_id=814851486621106198
    )
    assert not can_run_tts_usage(user_id=111, guild_id=814851486621106198)
    assert not can_run_tts_usage(user_id=354114876526034944, guild_id=999)
