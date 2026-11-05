"""Owner-ops allowlist."""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.app.ops_access import can_run_owner_ops

if TYPE_CHECKING:
    import pytest


def test_deny_when_allowlists_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BOT_OWNER_USER_IDS', raising=False)
    monkeypatch.delenv('BOT_OWNER_GUILD_IDS', raising=False)
    monkeypatch.delenv('TTS_USAGE_ALLOWED_USER_IDS', raising=False)
    monkeypatch.delenv('TTS_USAGE_ALLOWED_GUILD_IDS', raising=False)
    assert can_run_owner_ops(user_id=1, guild_id=2) is False


def test_owner_and_guild_must_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BOT_OWNER_USER_IDS', '354114876526034944')
    monkeypatch.setenv('BOT_OWNER_GUILD_IDS', '814851486621106198')
    assert (
        can_run_owner_ops(
            user_id=354114876526034944, guild_id=814851486621106198
        )
        is True
    )
    assert can_run_owner_ops(user_id=1, guild_id=814851486621106198) is False
    assert can_run_owner_ops(user_id=354114876526034944, guild_id=1) is False


def test_falls_back_to_tts_usage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('BOT_OWNER_USER_IDS', raising=False)
    monkeypatch.delenv('BOT_OWNER_GUILD_IDS', raising=False)
    monkeypatch.setenv('TTS_USAGE_ALLOWED_USER_IDS', '10')
    monkeypatch.setenv('TTS_USAGE_ALLOWED_GUILD_IDS', '20')
    assert can_run_owner_ops(user_id=10, guild_id=20) is True
    assert can_run_owner_ops(user_id=10, guild_id=99) is False
