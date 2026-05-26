import json

import discord
import pytest
from pydantic_core import ValidationError

from misarmy_talkbot.infra.config.config import validate_config
from misarmy_talkbot.infra.config.presence import (
    PresenceConfig,
    build_presence_activity,
    resolve_presence_name,
)


def test_presence_defaults() -> None:
    presence = PresenceConfig()
    assert presence.type == 'playing'
    assert presence.name is None


def test_resolve_presence_name_uses_gettext_default() -> None:
    name = resolve_presence_name(PresenceConfig(), guild=None)
    assert name == '/follow to talk in voice'


def test_presence_in_full_config() -> None:
    config = validate_config(
        json.dumps({
            'locale': 'en_US',
            'defaultVoice': 'edge/en-US-BrianMultilingualNeural',
            'replacements': {
                'regex': {},
                'emojis': {},
                'stickers': {},
                'mentions': {},
                'roles': {},
                'channels': {},
            },
            'voicePresets': {},
            'localeOverrides': {},
            'presence': {
                'type': 'listening',
                'name': 'TTS · /help',
            },
        })
    )
    assert config.presence.type == 'listening'
    assert config.presence.name == 'TTS · /help'


def test_presence_name_max_length() -> None:
    with pytest.raises(ValidationError):
        PresenceConfig(name='x' * 129)


def test_streaming_requires_url() -> None:
    with pytest.raises(ValidationError):
        PresenceConfig(type='streaming', name='live')


def test_build_presence_activity_playing() -> None:
    activity = build_presence_activity(
        PresenceConfig(type='playing', name='hello')
    )
    assert activity.type == discord.ActivityType.playing
    assert activity.name == 'hello'
