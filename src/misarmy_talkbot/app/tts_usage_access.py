"""Who may run ``/tts-usage`` (ops / billing visibility)."""

from __future__ import annotations

import os


def _parse_id_set(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for part in raw.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            values.add(int(part))
        except ValueError:
            continue
    return frozenset(values)


def allowed_tts_usage_user_ids() -> frozenset[int]:
    """Comma-separated Discord user IDs; empty → no user allowlist."""
    return _parse_id_set(os.getenv('TTS_USAGE_ALLOWED_USER_IDS', ''))


def allowed_tts_usage_guild_ids() -> frozenset[int]:
    """Comma-separated guild IDs; empty → any guild (still subject to user list)."""
    return _parse_id_set(os.getenv('TTS_USAGE_ALLOWED_GUILD_IDS', ''))


def can_run_tts_usage(*, user_id: int, guild_id: int) -> bool:
    """Return True if this user may see TTS usage in this guild.

    Rules (all configured lists must pass):
    - If ``TTS_USAGE_ALLOWED_USER_IDS`` is set → user must be in it.
    - If ``TTS_USAGE_ALLOWED_GUILD_IDS`` is set → guild must be in it.
    - If both are empty → deny (force explicit allowlist in env).
    """
    users = allowed_tts_usage_user_ids()
    guilds = allowed_tts_usage_guild_ids()
    if not users and not guilds:
        return False
    if users and user_id not in users:
        return False
    if guilds and guild_id not in guilds:
        return False
    return True
