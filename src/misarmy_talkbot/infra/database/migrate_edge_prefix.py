"""One-shot migration: user-facing voice prefix ``edge/`` → ``azure/``.

Updates SQLite rows and on-disk JSONC configs. Idempotent.
Backend metering names (``provider='edge'`` for edge-tts scrape) are unchanged.
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

_OLD_PREFIX = 'edge/'
_NEW_PREFIX = 'azure/'
# Voice ids in JSONC / JSONL look like "edge/en-US-..."
_VOICE_PREFIX_RE = re.compile(r'(?<![\w/])edge/')


def rewrite_edge_voice_prefix(value: str) -> str:
    """Replace leading ``edge/`` on a full voice id (no-op otherwise)."""
    if value.startswith(_OLD_PREFIX):
        return _NEW_PREFIX + value[len(_OLD_PREFIX) :]
    return value


async def migrate_edge_prefix_to_azure() -> None:
    """Rewrite stored ``edge/...`` voices to ``azure/...`` (DB + config files)."""
    db_updates = await _migrate_sqlite()
    config_updates = await _migrate_config_files()
    jsonl_updates = await _migrate_jsonl_files()
    logger.info(
        'migrate_edge_prefix_done db_rows=%s config_files=%s jsonl_files=%s',
        db_updates,
        config_updates,
        jsonl_updates,
    )


async def _migrate_sqlite() -> int:
    total = 0
    async with engine.begin() as conn:
        for table in ('user_voices', 'voice_presets', 'tts_usage_events'):
            # Table may not exist on a brand-new volume before create_all;
            # caller runs this after create_tables.
            result = await conn.execute(
                text(
                    f"UPDATE {table} SET voice = 'azure/' || substr(voice, 6) "
                    f"WHERE voice LIKE 'edge/%'"
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
        new_body = _VOICE_PREFIX_RE.sub(_NEW_PREFIX, text_body)
        if new_body != text_body:
            path.write_text(new_body, encoding='utf-8')
            updated += 1
            logger.info('migrate_edge_prefix_config path=%s', path)
    return updated


async def _migrate_jsonl_files() -> int:
    usage_dir = CONFIG_DIR / 'tts-usage'
    if not usage_dir.is_dir():
        return 0
    updated = 0
    for path in sorted(usage_dir.glob('*.jsonl')):
        text_body = path.read_text(encoding='utf-8')
        new_body = _VOICE_PREFIX_RE.sub(_NEW_PREFIX, text_body)
        if new_body != text_body:
            path.write_text(new_body, encoding='utf-8')
            updated += 1
    return updated
