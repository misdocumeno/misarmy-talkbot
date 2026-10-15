"""Prosody limits and Microsoft rate/pitch string mapping."""

from __future__ import annotations

import pytest

from misarmy_talkbot.core.playback.tts.prosody import (
    microsoft_pitch_string,
    microsoft_rate_string,
)
from misarmy_talkbot.core.playback.tts.provider import (
    GOOGLE_PROSODY,
    MICROSOFT_PROSODY,
)


def test_microsoft_rate_and_pitch_strings() -> None:
    assert microsoft_rate_string(1.0) == '+0%'
    assert microsoft_rate_string(1.25) == '+25%'
    assert microsoft_rate_string(0.75) == '-25%'
    assert microsoft_pitch_string(1.0) == '+0Hz'
    assert microsoft_pitch_string(1.1) == '+10Hz'
    assert microsoft_pitch_string(0.9) == '-10Hz'


def test_microsoft_prosody_bounds() -> None:
    MICROSOFT_PROSODY.validate_values(pitch=1.0, speed=1.0)
    with pytest.raises(ValueError, match='pitch'):
        MICROSOFT_PROSODY.validate_values(pitch=0.1, speed=1.0)
    with pytest.raises(ValueError, match='speed'):
        MICROSOFT_PROSODY.validate_values(pitch=1.0, speed=3.0)


def test_google_atempo_constraint() -> None:
    GOOGLE_PROSODY.validate_values(pitch=1.0, speed=1.0)
    # speed/pitch = 0.25 → below atempo floor 0.5 (still within pitch/speed)
    with pytest.raises(ValueError, match='atempo'):
        GOOGLE_PROSODY.validate_values(pitch=2.0, speed=0.5)
