"""Single-row store for the drain snapshot (versioned JSON text, no audio)."""

from datetime import UTC, datetime

from sqlalchemy import Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base, async_session


class DrainSnapshot(Base):
    __tablename__ = 'drain_snapshot'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC)
    )
    payload: Mapped[str] = mapped_column(Text)


async def save_snapshot(payload: str) -> int:
    """Insert the snapshot and commit before returning (the caller exits next)."""
    async with async_session() as session:
        row = DrainSnapshot(payload=payload)
        session.add(row)
        await session.commit()
        return row.id


async def load_latest_snapshot() -> tuple[int, str] | None:
    async with async_session() as session:
        result = await session.execute(
            select(DrainSnapshot).order_by(DrainSnapshot.id.desc()).limit(1)
        )
        row = result.scalar_one_or_none()
        return None if row is None else (row.id, row.payload)


async def delete_snapshot(snapshot_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            delete(DrainSnapshot).where(DrainSnapshot.id == snapshot_id)
        )
        await session.commit()
