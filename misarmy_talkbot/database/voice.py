import discord
from sqlalchemy import UniqueConstraint, insert, update
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.future import select
from .database import Base, async_session
from ..config.config import config


class UserVoice(Base):
    __tablename__ = 'user_voices'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[int]
    user_id: Mapped[int]
    voice: Mapped[str]
    pitch: Mapped[float] = mapped_column(default=1.0)
    speed: Mapped[float] = mapped_column(default=1.0)
    __table_args__ = (UniqueConstraint('guild_id', 'user_id', name='_guild_user_uc'),)

    def __repr__(self) -> str:
        return f'UserVoice(guild_id={self.guild_id}, user_id={self.user_id}, ' \
            f'voice={self.voice!r}, pitch={self.pitch}, speed={self.speed})'


_user_voices: dict[discord.Guild, dict[discord.Member, UserVoice]] = {}


async def get_voice(member: discord.Member) -> UserVoice:
    """Gets the voice setting from the database (caching it). Creates a new entry if it doesn't exist."""
    # we have a cached voice setting
    if member.guild in _user_voices and member in _user_voices[member.guild]:
        return _user_voices[member.guild][member]

    # get the setting from the database
    async with async_session() as session:
        result = await session.execute(select(UserVoice).filter_by(guild_id=member.guild.id, user_id=member.id))
        voice = result.scalar_one_or_none()

        # nothing in the db, create a new entry
        if voice is None:
            result = await session.execute(
                insert(UserVoice)
                .values(guild_id=member.guild.id, user_id=member.id, voice=config[member.guild].default_voice)
                .returning(UserVoice))
            await session.commit()
            voice = result.scalar_one()

    _user_voices.setdefault(member.guild, {})[member] = voice
    return voice


async def update_voice(
    member: discord.Member,
    *,
    voice: str | None = None,
    pitch: float | None = None,
    speed: float | None = None
) -> bool:
    """Updates the voice setting in the database (caching it)."""
    current = await get_voice(member)

    # reset pitch and speed when changing the voice
    if voice is not None:
        pitch = pitch if pitch is not None else 1.0
        speed = speed if speed is not None else 1.0

    new_voice = voice if voice is not None else current.voice
    new_pitch = pitch if pitch is not None else current.pitch
    new_speed = speed if speed is not None else current.speed

    if (new_voice, new_pitch, new_speed) == (current.voice, current.pitch, current.speed):
        return False

    async with async_session() as session:
        updated = await session.execute(
            update(UserVoice).filter_by(guild_id=member.guild.id, user_id=member.id)
            .values(voice=new_voice, pitch=new_pitch, speed=new_speed)
            .returning(UserVoice))
        await session.commit()

    _user_voices.setdefault(member.guild, {})[member] = updated.scalar_one()
    return True
