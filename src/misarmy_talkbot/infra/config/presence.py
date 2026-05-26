"""Map validated config presence to discord.py activities."""

from __future__ import annotations

from typing import Literal

import discord
from pydantic import BaseModel, Field, field_validator, model_validator

_PRESENCE_TYPES = Literal[
    'playing', 'listening', 'watching', 'competing', 'streaming'
]

_TYPE_TO_ACTIVITY: dict[_PRESENCE_TYPES, discord.ActivityType] = {
    'playing': discord.ActivityType.playing,
    'listening': discord.ActivityType.listening,
    'watching': discord.ActivityType.watching,
    'competing': discord.ActivityType.competing,
    'streaming': discord.ActivityType.streaming,
}


class PresenceConfig(BaseModel):
    """Sidebar subtitle shown for the bot (Rich Presence activity)."""

    type: _PRESENCE_TYPES = 'playing'
    name: str | None = Field(default=None, max_length=128)
    url: str | None = None

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            msg = 'presence.name must not be empty'
            raise ValueError(msg)
        return stripped

    @model_validator(mode='after')
    def streaming_url_rules(self) -> PresenceConfig:
        if self.type == 'streaming':
            if not self.url:
                msg = 'presence.url is required when type is streaming'
                raise ValueError(msg)
            lowered = self.url.lower()
            if 'twitch.tv' not in lowered and 'youtube.com' not in lowered:
                msg = 'streaming presence url must be a Twitch or YouTube URL'
                raise ValueError(msg)
        elif self.url:
            msg = 'presence.url is only valid when type is streaming'
            raise ValueError(msg)
        return self


def resolve_presence_name(
    presence: PresenceConfig, guild: discord.Guild | None = None
) -> str:
    """Config ``name`` overrides; otherwise gettext ``presence_default_name``."""
    if presence.name is not None:
        return presence.name
    from misarmy_talkbot.infra.locale.i18n import translate

    return translate('presence_default_name', guild)


def build_presence_activity(
    presence: PresenceConfig, *, guild: discord.Guild | None = None
) -> discord.Activity:
    """Build a discord.py Activity from validated config."""
    kwargs: dict[str, object] = {
        'type': _TYPE_TO_ACTIVITY[presence.type],
        'name': resolve_presence_name(presence, guild),
    }
    if presence.type == 'streaming' and presence.url is not None:
        kwargs['url'] = presence.url
    return discord.Activity(**kwargs)
