import os
import aiofiles
import aiofiles.os
import json5
import discord
from pydantic import BaseModel, Field
from ..locale import translations
from ..logger import logger


class ReplacementsSection(BaseModel):
    regex: dict[str, str]
    emojis: dict[str, str]
    stickers: dict[str, str]
    mentions: dict[str, str]
    roles: dict[str, str]
    channels: dict[str, str]


class VoicePreset(BaseModel):
    voice: str
    pitch: float
    speed: float


class GuildConfig(BaseModel):
    locale: str
    default_voice: str = Field(..., alias='defaultVoice')
    replacements: ReplacementsSection
    voice_presets: dict[str, VoicePreset] = Field(..., alias='voicePresets')
    locale_overrides: dict[str, str] = Field(..., alias='localeOverrides')


with open(os.path.join(os.path.dirname(__file__), 'default_config.jsonc')) as f:
    default_config = f.read()

_config_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'config'))
os.makedirs(os.path.join(_config_dir, 'guilds'), exist_ok=True)


def validate_config(config: str, guild: discord.Guild | None = None) -> GuildConfig:
    validated = GuildConfig.model_validate(json5.loads(config))
    # use preferred locale, if supported
    if guild is not None and validated.locale == 'guild_preferred':
        locale = guild.preferred_locale.value.replace('-', '_')
        if not translations.is_supported_locale(locale):
            logger.warning(f"{guild}'s preferred locale {locale!r} is not supported. Using global locale.")
            locale = translations.global_locale
        validated.locale = locale
    return validated


async def write_config_json(json: str, guild: discord.Guild | None = None):
    """Persists to disk the given config for the given guild (global if None)."""
    path = os.path.join(_config_dir, *(['global.jsonc'] if guild is None else ['guilds', f'{guild.id}.jsonc']))
    async with aiofiles.open(path, 'w') as f:
        await f.write(json)


async def get_config_json(guild: discord.Guild | None = None) -> str | None:
    """
    Returns the config of the specified guild (global if None) as a json string.
    None if there is no config on disk.
    """
    global global_config
    path = os.path.join(_config_dir, *(['global.jsonc'] if guild is None else ['guilds', f'{guild.id}.jsonc']))
    if await aiofiles.os.path.exists(path):
        async with aiofiles.open(path, 'r') as f:
            return await f.read()


async def update_config(json: str, guild: discord.Guild | None = None):
    """Sets the json as the new config in the specified guild (global if None), persisting it to disk."""
    global global_config, config
    guild_config = validate_config(json, guild)
    translations.validate_locale(guild_config.locale, guild)
    if guild is not None:
        config[guild] = guild_config
    else:
        global_config = guild_config
    await write_config_json(json, guild)


async def set_default_config(guild: discord.Guild | None = None):
    """
    Sets the config of the specified guild (global if None)
    to the default one, but without writing it to disk.
    """
    global global_config
    if guild is None:
        global_config = validate_config(default_config)
    else:
        config[guild] = validate_config(default_config, guild)


config: dict[discord.Guild, GuildConfig] = {}
global_config = validate_config(default_config)
