"""Owner-only ops allowlist (bot operator, not Discord guild owner role)."""

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


def _env_first(*names: str) -> str:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip() != '':
            return raw
    return ''


def owner_user_ids() -> frozenset[int]:
    """Comma-separated Discord user IDs for bot operators."""
    return _parse_id_set(
        _env_first('BOT_OWNER_USER_IDS', 'TTS_USAGE_ALLOWED_USER_IDS')
    )


def owner_guild_ids() -> frozenset[int]:
    """Comma-separated guild IDs where owner ops are allowed."""
    return _parse_id_set(
        _env_first('BOT_OWNER_GUILD_IDS', 'TTS_USAGE_ALLOWED_GUILD_IDS')
    )


def can_run_owner_ops(*, user_id: int, guild_id: int) -> bool:
    """True if this user may run owner-tier ops in this guild.

    - If user allowlist is set → user must match.
    - If guild allowlist is set → guild must match.
    - If both empty → deny (force explicit env allowlist).
    """
    users = owner_user_ids()
    guilds = owner_guild_ids()
    if not users and not guilds:
        return False
    if users and user_id not in users:
        return False
    if guilds and guild_id not in guilds:
        return False
    return True
