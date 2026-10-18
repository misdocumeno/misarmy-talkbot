"""Per-guild auto-assign rotation cursor."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from misarmy_talkbot.infra.database.database import Base
from misarmy_talkbot.infra.database.voice_rotation import (
    GuildVoiceRotation,
    take_next_rotation_index,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pytest import MonkeyPatch


@pytest.mark.asyncio
async def test_rotation_increments(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    assert GuildVoiceRotation.__tablename__ == 'guild_voice_rotation'
    db_path = tmp_path / 'rot.db'
    engine = create_async_engine(f'sqlite+aiosqlite:///{db_path}')
    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    monkeypatch.setattr(
        'misarmy_talkbot.infra.database.voice_rotation.async_session',
        session_factory,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    assert await take_next_rotation_index(42) == 0
    assert await take_next_rotation_index(42) == 1
    assert await take_next_rotation_index(42) == 2
    assert await take_next_rotation_index(99) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_next_auto_voice_cycles_pool() -> None:
    from misarmy_talkbot.core.playback.tts import voice_pool as vp

    pool = ['ms/es-AR-ANeural', 'ms/es-ES-BNeural', 'google/es-US-Standard-A']
    with (
        patch.object(
            vp, 'cheap_pool_for_language', new=AsyncMock(return_value=pool)
        ),
        patch(
            'misarmy_talkbot.infra.database.voice_rotation.take_next_rotation_index',
            new=AsyncMock(side_effect=[0, 1, 2, 3]),
        ),
    ):
        assert await vp.next_auto_voice_for_guild(1, 'es') == pool[0]
        assert await vp.next_auto_voice_for_guild(1, 'es') == pool[1]
        assert await vp.next_auto_voice_for_guild(1, 'es') == pool[2]
        assert await vp.next_auto_voice_for_guild(1, 'es') == pool[0]
