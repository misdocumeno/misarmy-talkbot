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
    name: str = Field(
        default='/follow to talk in voice',
        max_length=128,
    )
    url: str | None = None

    @field_validator('name')
    @classmethod
    def name_not_blank(cls, value: str) -> str:
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


def build_presence_activity(presence: PresenceConfig) -> discord.Activity:
    """Build a discord.py Activity from validated config."""
    kwargs: dict[str, object] = {
        'type': _TYPE_TO_ACTIVITY[presence.type],
        'name': presence.name,
    }
    if presence.type == 'streaming' and presence.url is not None:
        kwargs['url'] = presence.url
    return discord.Activity(**kwargs)
