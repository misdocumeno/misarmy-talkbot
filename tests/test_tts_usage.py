"""TTS usage store (SQLite + JSONL) and cost helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from misarmy_talkbot.core.playback.tts.cost import estimate_usd
from misarmy_talkbot.infra.database.database import Base
from misarmy_talkbot.infra.database.tts_usage import (
    TtsUsageEvent,
    TtsUsageStore,
    UsageRecord,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_estimate_usd_azure_free_tier() -> None:
    assert estimate_usd(provider='azure', chars=100_000) == 0.0
    # 600k → 100k billable at $16/M = $1.60
    assert estimate_usd(provider='azure', chars=600_000) == pytest.approx(1.6)
    assert estimate_usd(provider='google', chars=1_000_000) == 0.0
    assert estimate_usd(provider='edge', chars=1_000_000) == 0.0
    # Chirp: 1M free, then $30/M → 1.5M chars → $15
    assert estimate_usd(provider='google_cloud', chars=500_000) == 0.0
    assert estimate_usd(
        provider='google_cloud', chars=1_500_000
    ) == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_record_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / 'usage.db'
    test_engine = create_async_engine(
        f'sqlite+aiosqlite:///{db_path.resolve()}'
    )
    test_session = async_sessionmaker(
        test_engine, expire_on_commit=False, class_=AsyncSession
    )
    monkeypatch.setattr(
        'misarmy_talkbot.infra.database.tts_usage.async_session',
        test_session,
    )

    async with test_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=[TtsUsageEvent.__table__]
            )
        )

    store = TtsUsageStore(jsonl_dir=tmp_path / 'tts-usage')
    TtsUsageStore.set_instance(store)
    try:
        await store.record(
            UsageRecord(
                ts=datetime.now(UTC),
                guild_id=1,
                user_id=10,
                provider='azure',
                voice='ms/es-AR-ElenaNeural',
                chars=100,
                ok=True,
                latency_ms=50,
            )
        )
        await store.record(
            UsageRecord(
                ts=datetime.now(UTC),
                guild_id=1,
                user_id=11,
                provider='google',
                voice='google/es-Spanish',
                chars=40,
                ok=True,
                latency_ms=80,
            )
        )
        jsonl_files = list(store._jsonl_dir.glob('*.jsonl'))
        assert len(jsonl_files) == 1
        assert jsonl_files[0].read_text(encoding='utf-8').count('\n') == 2

        assert await store.azure_chars_this_utc_month() == 100

        report = await store.build_report(
            days=30,
            guild_id=1,
            azure_month_cap=500_000,
            google_cloud_month_cap=1_000_000,
        )
        assert report.total_chars == 140
        assert report.total_requests == 2
        assert report.azure_month_chars == 100
        assert report.google_cloud_month_cap == 1_000_000
        assert {item.provider for item in report.by_provider} == {
            'azure',
            'google',
        }
    finally:
        TtsUsageStore.reset()
        await test_engine.dispose()
