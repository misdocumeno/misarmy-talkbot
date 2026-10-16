"""Durable TTS usage events (SQLite) + daily JSONL audit files."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import aiofiles
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column  # noqa: TC002

from misarmy_talkbot.infra.database.database import Base, async_session
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.paths import CONFIG_DIR

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Select


class TtsUsageEvent(Base):
    __tablename__ = 'tts_usage_events'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    ts: Mapped[str] = mapped_column(String(32), index=True)
    guild_id: Mapped[int] = mapped_column(Integer, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    voice: Mapped[str] = mapped_column(String(128))
    chars: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)


class UsageRecord(BaseModel):
    """One synthesis attempt for metering / JSONL."""

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    guild_id: int
    user_id: int
    provider: str
    voice: str
    chars: int = Field(ge=0)
    ok: bool
    latency_ms: int = Field(ge=0)
    error: str | None = None


class ProviderUsageSummary(BaseModel):
    provider: str
    chars: int
    requests: int
    est_usd: float


class UsageReport(BaseModel):
    days: int
    since: datetime
    total_chars: int
    total_requests: int
    by_provider: list[ProviderUsageSummary]
    top_guilds: list[tuple[int, int]]
    top_users: list[tuple[int, int]]
    guild_top_users: list[tuple[int, int]]
    azure_month_chars: int
    azure_month_cap: int
    google_cloud_month_chars: int
    google_cloud_month_cap: int


class TtsUsageStore:
    """Append usage rows; never raise into the TTS hot path."""

    _instance: TtsUsageStore | None = None

    def __init__(self, *, jsonl_dir: Path | None = None) -> None:
        self._jsonl_dir = jsonl_dir or (CONFIG_DIR / 'tts-usage')

    @classmethod
    def instance(cls) -> TtsUsageStore:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @classmethod
    def set_instance(cls, store: TtsUsageStore) -> None:
        cls._instance = store

    async def record(self, record: UsageRecord) -> None:
        try:
            await self._insert_sqlite(record)
        except Exception:
            logger.exception('tts_usage_sqlite_failed')
        try:
            await self._append_jsonl(record)
        except Exception:
            logger.exception('tts_usage_jsonl_failed')

    async def azure_chars_this_utc_month(self) -> int:
        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        return await self._sum_chars(
            provider='azure',
            since=month_start,
            ok_only=True,
        )

    async def google_cloud_chars_this_utc_month(self) -> int:
        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        return await self._sum_chars(
            provider='google_cloud',
            since=month_start,
            ok_only=True,
        )

    async def build_report(
        self,
        *,
        days: int,
        guild_id: int | None,
        azure_month_cap: int,
        google_cloud_month_cap: int = 0,
    ) -> UsageReport:
        since = datetime.now(UTC) - timedelta(days=days)
        since_iso = _to_iso(since)

        async with async_session() as session:
            total_row = (
                await session.execute(
                    select(
                        func.coalesce(func.sum(TtsUsageEvent.chars), 0),
                        func.count(TtsUsageEvent.id),
                    ).where(
                        TtsUsageEvent.ts >= since_iso,
                        TtsUsageEvent.ok.is_(True),
                    )
                )
            ).one()
            total_chars = int(total_row[0])
            total_requests = int(total_row[1])

            by_provider_rows = (
                await session.execute(
                    select(
                        TtsUsageEvent.provider,
                        func.coalesce(func.sum(TtsUsageEvent.chars), 0),
                        func.count(TtsUsageEvent.id),
                    )
                    .where(
                        TtsUsageEvent.ts >= since_iso,
                        TtsUsageEvent.ok.is_(True),
                    )
                    .group_by(TtsUsageEvent.provider)
                )
            ).all()

            top_guilds = (
                await session.execute(
                    select(
                        TtsUsageEvent.guild_id,
                        func.coalesce(func.sum(TtsUsageEvent.chars), 0),
                    )
                    .where(
                        TtsUsageEvent.ts >= since_iso,
                        TtsUsageEvent.ok.is_(True),
                    )
                    .group_by(TtsUsageEvent.guild_id)
                    .order_by(func.sum(TtsUsageEvent.chars).desc())
                    .limit(5)
                )
            ).all()

            top_users = (
                await session.execute(
                    select(
                        TtsUsageEvent.user_id,
                        func.coalesce(func.sum(TtsUsageEvent.chars), 0),
                    )
                    .where(
                        TtsUsageEvent.ts >= since_iso,
                        TtsUsageEvent.ok.is_(True),
                    )
                    .group_by(TtsUsageEvent.user_id)
                    .order_by(func.sum(TtsUsageEvent.chars).desc())
                    .limit(5)
                )
            ).all()

            guild_top_rows: list[tuple[int, int]] = []
            if guild_id is not None:
                guild_top_result = (
                    await session.execute(
                        select(
                            TtsUsageEvent.user_id,
                            func.coalesce(func.sum(TtsUsageEvent.chars), 0),
                        )
                        .where(
                            TtsUsageEvent.ts >= since_iso,
                            TtsUsageEvent.ok.is_(True),
                            TtsUsageEvent.guild_id == guild_id,
                        )
                        .group_by(TtsUsageEvent.user_id)
                        .order_by(func.sum(TtsUsageEvent.chars).desc())
                        .limit(5)
                    )
                ).all()
                guild_top_rows = [
                    (int(row[0]), int(row[1])) for row in guild_top_result
                ]

        from misarmy_talkbot.core.playback.tts.cost import estimate_usd

        by_provider = [
            ProviderUsageSummary(
                provider=str(row[0]),
                chars=int(row[1]),
                requests=int(row[2]),
                est_usd=estimate_usd(provider=str(row[0]), chars=int(row[1])),
            )
            for row in by_provider_rows
        ]
        by_provider.sort(key=lambda item: item.chars, reverse=True)

        return UsageReport(
            days=days,
            since=since,
            total_chars=total_chars,
            total_requests=total_requests,
            by_provider=by_provider,
            top_guilds=[(int(row[0]), int(row[1])) for row in top_guilds],
            top_users=[(int(row[0]), int(row[1])) for row in top_users],
            guild_top_users=guild_top_rows,
            azure_month_chars=await self.azure_chars_this_utc_month(),
            azure_month_cap=azure_month_cap,
            google_cloud_month_chars=(
                await self.google_cloud_chars_this_utc_month()
            ),
            google_cloud_month_cap=google_cloud_month_cap,
        )

    async def _sum_chars(
        self, *, provider: str, since: datetime, ok_only: bool
    ) -> int:
        statement: Select[tuple[int]] = select(
            func.coalesce(func.sum(TtsUsageEvent.chars), 0)
        ).where(
            TtsUsageEvent.provider == provider,
            TtsUsageEvent.ts >= _to_iso(since),
        )
        if ok_only:
            statement = statement.where(TtsUsageEvent.ok.is_(True))
        async with async_session() as session:
            result = await session.execute(statement)
            return int(result.scalar_one())

    async def _insert_sqlite(self, record: UsageRecord) -> None:
        async with async_session() as session:
            session.add(
                TtsUsageEvent(
                    ts=_to_iso(record.ts),
                    guild_id=record.guild_id,
                    user_id=record.user_id,
                    provider=record.provider,
                    voice=record.voice,
                    chars=record.chars,
                    ok=record.ok,
                    latency_ms=record.latency_ms,
                    error=(record.error[:512] if record.error else None),
                )
            )
            await session.commit()

    async def _append_jsonl(self, record: UsageRecord) -> None:
        self._jsonl_dir.mkdir(parents=True, exist_ok=True)
        day = record.ts.astimezone(UTC).date().isoformat()
        path = self._jsonl_dir / f'{day}.jsonl'
        payload = {
            'ts': _to_iso(record.ts),
            'guild_id': record.guild_id,
            'user_id': record.user_id,
            'provider': record.provider,
            'voice': record.voice,
            'chars': record.chars,
            'ok': record.ok,
            'latency_ms': record.latency_ms,
            'error': record.error,
        }
        line = json.dumps(payload, ensure_ascii=False) + '\n'
        async with aiofiles.open(path, mode='a', encoding='utf-8') as handle:
            await handle.write(line)


def _to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
