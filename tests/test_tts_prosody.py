"""Prosody limits from official docs + ffmpeg atempo math."""

from __future__ import annotations

import pytest

from misarmy_talkbot.core.playback.tts.effects import split_atempo_factors
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


def test_microsoft_matches_azure_ssml_docs() -> None:
    """Azure Learn: pitch 0.5–1.5×, rate 0.5–2×."""
    assert MICROSOFT_PROSODY.pitch_min == 0.5
    assert MICROSOFT_PROSODY.pitch_max == 1.5
    assert MICROSOFT_PROSODY.speed_min == 0.5
    assert MICROSOFT_PROSODY.speed_max == 2.0
    MICROSOFT_PROSODY.validate_values(pitch=0.5, speed=2.0)
    MICROSOFT_PROSODY.validate_values(pitch=1.5, speed=0.5)
    with pytest.raises(ValueError, match='pitch'):
        MICROSOFT_PROSODY.validate_values(pitch=0.49, speed=1.0)
    with pytest.raises(ValueError, match='speed'):
        MICROSOFT_PROSODY.validate_values(pitch=1.0, speed=2.01)


def test_google_rejects_only_non_positive() -> None:
    GOOGLE_PROSODY.validate_values(pitch=1.0, speed=1.0)
    # Outside Azure windows is fine for ffmpeg-backed google voices.
    GOOGLE_PROSODY.validate_values(pitch=2.0, speed=0.5)
    GOOGLE_PROSODY.validate_values(pitch=0.1, speed=3.0)
    with pytest.raises(ValueError, match='pitch'):
        GOOGLE_PROSODY.validate_values(pitch=0.0, speed=1.0)
    with pytest.raises(ValueError, match='speed'):
        GOOGLE_PROSODY.validate_values(pitch=1.0, speed=-1.0)


def test_ffmpeg_atempo_chain_matches_docs() -> None:
    """Each factor in [0.5, 100]; product preserved (ffmpeg daisy-chain)."""
    factors = split_atempo_factors(0.25)
    assert all(0.5 <= factor <= 100.0 for factor in factors)
    assert pytest.approx(0.25, rel=1e-9) == _product(factors)
    factors_fast = split_atempo_factors(300.0)
    assert all(0.5 <= factor <= 100.0 for factor in factors_fast)
    assert pytest.approx(300.0, rel=1e-9) == _product(factors_fast)


def test_split_atempo_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match='atempo'):
        split_atempo_factors(0.0)


def _product(values: list[float]) -> float:
    result = 1.0
    for value in values:
        result *= value
    return result
