"""Google Cloud TTS billing bucket classification."""

from __future__ import annotations

from misarmy_talkbot.core.playback.tts.google_billing import (
    classify_google_voice,
    google_voice_sort_key,
    voice_allowed_in_catalog,
)


def test_classify_shared_skus() -> None:
    assert classify_google_voice('es-US-Standard-A') == 'standard_wavenet'
    assert classify_google_voice('en-US-Wavenet-D') == 'standard_wavenet'
    assert classify_google_voice('es-US-Neural2-A') == 'neural2'
    assert classify_google_voice('en-US-Polyglot-1') == 'neural2'
    assert classify_google_voice('es-US-Chirp3-HD-Aoede') == 'chirp3'
    assert classify_google_voice('google/es-US-Chirp3-HD-Aoede') == 'chirp3'
    assert classify_google_voice('en-US-Studio-O') == 'studio'
    assert classify_google_voice('en-US-InstantCustomVoice-X') == 'unsupported'


def test_catalog_excludes_studio() -> None:
    assert voice_allowed_in_catalog('es-US-Chirp3-HD-Aoede', voice_tier='all')
    assert voice_allowed_in_catalog('es-US-Standard-A', voice_tier='all')
    assert not voice_allowed_in_catalog('en-US-Studio-O', voice_tier='all')
    assert voice_allowed_in_catalog(
        'en-US-Studio-O', voice_tier='all', allow_studio=True
    )
    assert not voice_allowed_in_catalog(
        'en-US-InstantCustomVoice-X', voice_tier='all'
    )
    assert voice_allowed_in_catalog(
        'es-US-Chirp3-HD-Aoede', voice_tier='Chirp3'
    )
    assert not voice_allowed_in_catalog(
        'es-US-Standard-A', voice_tier='Chirp3'
    )


def test_google_voice_sort_cheap_first() -> None:
    voices = [
        'es-US-Chirp3-HD-Aoede',
        'es-US-Neural2-A',
        'es-US-Standard-A',
        'es-US-Wavenet-B',
    ]
    assert sorted(voices, key=google_voice_sort_key) == [
        'es-US-Standard-A',
        'es-US-Wavenet-B',
        'es-US-Neural2-A',
        'es-US-Chirp3-HD-Aoede',
    ]
