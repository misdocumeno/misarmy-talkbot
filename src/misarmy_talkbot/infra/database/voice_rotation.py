"""Per-guild circular index for auto-assign voice pools."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Mapped, mapped_column  # noqa: TC002

from misarmy_talkbot.infra.database.database import Base, async_session


class GuildVoiceRotation(Base):
    __tablename__ = 'guild_voice_rotation'

    guild_id: Mapped[int] = mapped_column(primary_key=True)
    next_index: Mapped[int] = mapped_column(default=0)


async def take_next_rotation_index(guild_id: int) -> int:
    """Return current index and advance by 1 (caller applies ``% len(pool)``)."""
    async with async_session() as session:
        result = await session.execute(
            select(GuildVoiceRotation).where(
                GuildVoiceRotation.guild_id == guild_id
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = GuildVoiceRotation(guild_id=guild_id, next_index=0)
            session.add(row)
            await session.flush()
        index = row.next_index
        row.next_index = index + 1
        await session.commit()
        return index
