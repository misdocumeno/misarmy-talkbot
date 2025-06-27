import discord
from sqlalchemy import UniqueConstraint, insert
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.future import select
from .database import Base, async_session
from ..config.config import config


class VoicePreset(Base):
    __tablename__ = 'voice_presets'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[int]
    user_id: Mapped[int]
    name: Mapped[str]
    voice: Mapped[str]
    pitch: Mapped[float]
    speed: Mapped[float]
    __table_args__ = (UniqueConstraint('guild_id', 'user_id', 'name', name='uix_guild_user_name'),)

    def __repr__(self) -> str:
        return f'VoicePreset(guild_id={self.guild_id}, user_id={self.user_id}, ' \
            f'name={self.name!r}, voice={self.voice!r}, pitch={self.pitch}, speed={self.speed})'


_user_presets: dict[discord.Guild, dict[discord.Member, list[VoicePreset]]] = {}


async def get_presets(member: discord.Member) -> list[VoicePreset]:
    """Gets the saved voice presets from the database (caching it)."""
    # we have a cached voice presets list
    if member.guild in _user_presets and member in _user_presets[member.guild]:
        return _user_presets[member.guild][member] + get_guild_presets(member.guild)

    # get it from the database
    async with async_session() as session:
        result = await session.execute(select(VoicePreset).filter_by(guild_id=member.guild.id, user_id=member.id))
        presets = list(result.scalars())

    _user_presets.setdefault(member.guild, {})[member] = presets
    return presets + get_guild_presets(member.guild)


async def get_preset(member: discord.Member, name: str) -> VoicePreset | None:
    """Gets a preset from the database (caching it)."""
    presets = await get_presets(member)
    return next((preset for preset in presets if preset.name == name), None)


async def save_preset(member: discord.Member, name: str, voice: str, pitch: float, speed: float) -> bool:
    """Saves a new preset into the database (caching it)."""
    if await get_preset(member, name) is not None:
        return False

    async with async_session() as session:
        result = await session.execute(
            insert(VoicePreset)
            .values(guild_id=member.guild.id, user_id=member.id, name=name, voice=voice, pitch=pitch, speed=speed)
            .returning(VoicePreset))
        await session.commit()
        preset = result.scalar_one()

    _user_presets[member.guild][member].append(preset)
    return True


async def delete_preset(member: discord.Member, name: str) -> bool:
    preset = await get_preset(member, name)

    if preset is None or preset.guild_id == 0:
        return False

    async with async_session() as session:
        await session.delete(preset)
        await session.commit()

    _user_presets[member.guild][member].remove(preset)
    return True


def get_guild_presets(guild: discord.Guild) -> list[VoicePreset]:
    return [
        VoicePreset(guild_id=guild.id, user_id=0, name=name, voice=preset.voice, pitch=preset.pitch, speed=preset.speed)
        for name, preset in config[guild].voice_presets.items()
    ]
