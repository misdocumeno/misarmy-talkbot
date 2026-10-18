"""One-shot migration: user-facing voice prefixes ``edge/`` / ``azure/`` → ``ms/``.

Updates SQLite rows and on-disk JSONC / JSONL. Idempotent.
Backend metering names (``provider='azure'`` / ``'edge'``) are unchanged.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import text

from misarmy_talkbot.infra.database.database import engine
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.paths import CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

_NEW_PREFIX = 'ms/'
# Match edge/ or azure/ as a voice-id prefix in free text (JSONC / JSONL).
_LEGACY_VOICE_PREFIX_RE = re.compile(r'(?<![\w/])(?:edge|azure)/')


def rewrite_legacy_voice_prefix(value: str) -> str:
    """Replace leading ``edge/`` or ``azure/`` on a full voice id."""
    for old in ('edge/', 'azure/'):
        if value.startswith(old):
            return _NEW_PREFIX + value[len(old) :]
    return value


async def migrate_voice_prefix_to_ms() -> None:
    """Rewrite stored ``edge/...`` / ``azure/...`` voices to ``ms/...``."""
    db_updates = await _migrate_sqlite()
    config_updates = await _migrate_config_files()
    jsonl_updates = await _migrate_jsonl_files()
    logger.info(
        'migrate_voice_prefix_done db_rows=%s config_files=%s jsonl_files=%s',
        db_updates,
        config_updates,
        jsonl_updates,
    )


async def _migrate_sqlite() -> int:
    total = 0
    async with engine.begin() as conn:
        for table in ('user_voices', 'voice_presets', 'tts_usage_events'):
            for old in ('edge/', 'azure/'):
                # edge/ → substr from 6; azure/ → substr from 7
                start = len(old) + 1
                result = await conn.execute(
                    text(
                        f"UPDATE {table} SET voice = 'ms/' || substr(voice, {start}) "
                        f"WHERE voice LIKE '{old}%'"
                    )
                )
                total += result.rowcount or 0
    return total


async def _migrate_config_files() -> int:
    updated = 0
    paths: list[Path] = []
    global_path = CONFIG_DIR / 'global.jsonc'
    if global_path.is_file():
        paths.append(global_path)
    guilds_dir = CONFIG_DIR / 'guilds'
    if guilds_dir.is_dir():
        paths.extend(sorted(guilds_dir.glob('*.jsonc')))
    for path in paths:
        text_body = path.read_text(encoding='utf-8')
        new_body = _LEGACY_VOICE_PREFIX_RE.sub(_NEW_PREFIX, text_body)
        if new_body != text_body:
            path.write_text(new_body, encoding='utf-8')
            updated += 1
            logger.info('migrate_voice_prefix_config path=%s', path)
    return updated


async def _migrate_jsonl_files() -> int:
    usage_dir = CONFIG_DIR / 'tts-usage'
    if not usage_dir.is_dir():
        return 0
    updated = 0
    for path in sorted(usage_dir.glob('*.jsonl')):
        text_body = path.read_text(encoding='utf-8')
        new_body = _LEGACY_VOICE_PREFIX_RE.sub(_NEW_PREFIX, text_body)
        if new_body != text_body:
            path.write_text(new_body, encoding='utf-8')
            updated += 1
    return updated
