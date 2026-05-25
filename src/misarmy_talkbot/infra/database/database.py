from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from misarmy_talkbot.paths import CONFIG_DIR


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    'sqlite+aiosqlite:///' + str((CONFIG_DIR / 'user_settings.db').resolve())
)
async_session = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def create_tables() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
