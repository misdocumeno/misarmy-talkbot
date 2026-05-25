from pathlib import Path

import aiofiles
import aiofiles.os
import discord
import json5
from pydantic import BaseModel, Field

from misarmy_talkbot.infra.config.presence import PresenceConfig
from misarmy_talkbot.infra.locale import translations
from misarmy_talkbot.infra.locale.context import LocaleContext
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.paths import CONFIG_DIR


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
    presence: PresenceConfig = Field(default_factory=PresenceConfig)


with Path(__file__).with_name('default_config.jsonc').open() as f:
    default_config = f.read()

(CONFIG_DIR / 'guilds').mkdir(parents=True, exist_ok=True)


def locale_context(guild: discord.Guild | None) -> LocaleContext:
    """Build gettext inputs for a guild (or global defaults when ``guild`` is None)."""
    if guild is None:
        return LocaleContext(locale=translations.global_locale, overrides={})
    guild_config = config.get(guild)
    if guild_config is None:
        return LocaleContext(locale=translations.global_locale, overrides={})
    return LocaleContext(
        locale=guild_config.locale,
        overrides=guild_config.locale_overrides,
    )


def _config_path(guild: discord.Guild | None) -> Path:
    if guild is None:
        return CONFIG_DIR / 'global.jsonc'
    return CONFIG_DIR / 'guilds' / f'{guild.id}.jsonc'


def validate_config(
    config: str, guild: discord.Guild | None = None
) -> GuildConfig:
    validated = GuildConfig.model_validate(json5.loads(config))
    # use preferred locale, if supported
    if guild is not None and validated.locale == 'guild_preferred':
        locale = guild.preferred_locale.value.replace('-', '_')
        if not translations.is_supported_locale(locale):
            logger.warning(
                f"{guild}'s preferred locale {locale!r} is not supported. Using global locale."
            )
            locale = translations.global_locale
        validated.locale = locale
    return validated


async def write_config_json(
    json: str, guild: discord.Guild | None = None
) -> None:
    """Persists to disk the given config for the given guild (global if None)."""
    path = _config_path(guild)
    async with aiofiles.open(path, 'w') as f:
        await f.write(json)


async def get_config_json(guild: discord.Guild | None = None) -> str | None:
    """
    Returns the config of the specified guild (global if None) as a json string.
    None if there is no config on disk.
    """
    global global_config
    path = _config_path(guild)
    if await aiofiles.os.path.exists(path):
        async with aiofiles.open(path) as f:
            return await f.read()


async def apply_global_presence(bot: discord.Client) -> None:
    """Set bot sidebar activity from ``global_config.presence``."""
    from misarmy_talkbot.infra.config.presence import build_presence_activity

    activity = build_presence_activity(global_config.presence, guild=None)
    await bot.change_presence(activity=activity)
    logger.info(
        'presence_applied type=%s name=%r',
        global_config.presence.type,
        activity.name,
    )


async def update_config(json: str, guild: discord.Guild | None = None) -> None:
    """Sets the json as the new config in the specified guild (global if None), persisting it to disk."""
    global global_config, config
    guild_config = validate_config(json, guild)
    translations.validate_locale(guild_config.locale, guild)
    if guild is not None:
        config[guild] = guild_config
    else:
        global_config = guild_config
    await write_config_json(json, guild)


async def set_default_config(guild: discord.Guild | None = None) -> None:
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
