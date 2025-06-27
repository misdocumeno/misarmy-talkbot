import os
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    'sqlite+aiosqlite:///' +
    os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'user_settings.db')))
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
