"""Who may run ``/tts-usage`` (ops / billing visibility)."""

from __future__ import annotations

from misarmy_talkbot.app.ops_access import can_run_owner_ops


def can_run_tts_usage(*, user_id: int, guild_id: int) -> bool:
    """Same allowlist as other owner-tier ops (BOT_OWNER_* / legacy TTS_USAGE_*)."""
    return can_run_owner_ops(user_id=user_id, guild_id=guild_id)
