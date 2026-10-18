"""Migration: edge/ voice prefix → azure/."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Import models so Base.metadata includes their tables.
from misarmy_talkbot.infra.database import preset as _preset  # noqa: F401
from misarmy_talkbot.infra.database import (
    tts_usage as _tts_usage,  # noqa: F401
)
from misarmy_talkbot.infra.database import voice as _voice  # noqa: F401
from misarmy_talkbot.infra.database.database import Base
from misarmy_talkbot.infra.database.migrate_edge_prefix import (
    migrate_edge_prefix_to_azure,
    rewrite_edge_voice_prefix,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


def test_rewrite_edge_voice_prefix() -> None:
    assert (
        rewrite_edge_voice_prefix('edge/es-AR-ElenaNeural')
        == 'azure/es-AR-ElenaNeural'
    )
    assert (
        rewrite_edge_voice_prefix('azure/es-AR-ElenaNeural')
        == 'azure/es-AR-ElenaNeural'
    )
    assert rewrite_edge_voice_prefix('google/es') == 'google/es'


@pytest.mark.asyncio
async def test_migrate_sqlite_and_config(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr(
        'misarmy_talkbot.infra.database.migrate_edge_prefix.CONFIG_DIR',
        tmp_path,
    )
    db_path = tmp_path / 'user_settings.db'
    test_engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    monkeypatch.setattr(
        'misarmy_talkbot.infra.database.migrate_edge_prefix.engine',
        test_engine,
    )

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                'INSERT INTO user_voices '
                '(guild_id, user_id, voice, pitch, speed) '
                "VALUES (1, 2, 'edge/es-AR-ElenaNeural', 1.0, 1.0)"
            )
        )
        await conn.execute(
            text(
                'INSERT INTO voice_presets '
                '(guild_id, user_id, name, voice, pitch, speed) '
                "VALUES (1, 2, 'x', 'edge/en-US-JennyNeural', 1.0, 1.0)"
            )
        )
        await conn.execute(
            text(
                'INSERT INTO tts_usage_events '
                '(ts, guild_id, user_id, provider, voice, chars, ok, '
                'latency_ms, error) '
                "VALUES ('2026-01-01T00:00:00+00:00', 1, 2, 'azure', "
                "'edge/es-AR-ElenaNeural', 10, 1, 1, NULL)"
            )
        )

    (tmp_path / 'guilds').mkdir()
    (tmp_path / 'global.jsonc').write_text(
        '{"defaultVoice": "edge/en-US-BrianMultilingualNeural"}',
        encoding='utf-8',
    )
    usage_dir = tmp_path / 'tts-usage'
    usage_dir.mkdir()
    (usage_dir / '2026-01-01.jsonl').write_text(
        '{"voice":"edge/es-AR-ElenaNeural"}\n',
        encoding='utf-8',
    )

    await migrate_edge_prefix_to_azure()

    async with test_engine.connect() as conn:
        voice = (
            await conn.execute(text('SELECT voice FROM user_voices'))
        ).scalar_one()
        preset = (
            await conn.execute(text('SELECT voice FROM voice_presets'))
        ).scalar_one()
        usage = (
            await conn.execute(text('SELECT voice FROM tts_usage_events'))
        ).scalar_one()
    assert voice == 'azure/es-AR-ElenaNeural'
    assert preset == 'azure/en-US-JennyNeural'
    assert usage == 'azure/es-AR-ElenaNeural'
    assert 'azure/en-US-BrianMultilingualNeural' in (
        tmp_path / 'global.jsonc'
    ).read_text(encoding='utf-8')
    assert 'azure/es-AR-ElenaNeural' in (
        usage_dir / '2026-01-01.jsonl'
    ).read_text(encoding='utf-8')

    await test_engine.dispose()
