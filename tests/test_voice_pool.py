"""Cheap auto-assign voice pool filtering."""

from __future__ import annotations

from misarmy_talkbot.core.playback.tts.voice_pool import (
    AUTO_PITCH_MAX,
    AUTO_PITCH_MIN,
    is_auto_assign_voice,
    is_google_auto_assign_voice,
    is_ms_auto_assign_voice,
    random_auto_pitch,
    voice_language,
)


def test_voice_language() -> None:
    assert voice_language('ms/es-AR-ElenaNeural') == 'es'
    assert voice_language('google/es-US-Standard-A') == 'es'
    assert voice_language('google/en-US-Neural2-A') == 'en'


def test_ms_auto_assign_excludes_hd() -> None:
    assert is_ms_auto_assign_voice('es-AR-ElenaNeural')
    assert is_ms_auto_assign_voice('es-MX-DaliaMultilingualNeural')
    assert not is_ms_auto_assign_voice('en-US-Ava:DragonHDLatestNeural')
    assert not is_ms_auto_assign_voice('en-US-JennyNeuralHD')


def test_google_auto_assign_buckets() -> None:
    assert is_google_auto_assign_voice('es-US-Standard-A')
    assert is_google_auto_assign_voice('es-ES-Wavenet-B')
    assert is_google_auto_assign_voice('es-US-Neural2-A')
    assert not is_google_auto_assign_voice('es-US-Chirp3-HD-Aoede')
    assert not is_google_auto_assign_voice('en-US-Studio-O')


def test_full_id_filter() -> None:
    assert is_auto_assign_voice('ms/es-AR-ElenaNeural')
    assert is_auto_assign_voice('google/es-US-Standard-A')
    assert not is_auto_assign_voice('google/es-US-Chirp3-HD-Aoede')


def test_random_auto_pitch_in_band() -> None:
    for _ in range(40):
        pitch = random_auto_pitch()
        assert AUTO_PITCH_MIN <= pitch <= AUTO_PITCH_MAX
