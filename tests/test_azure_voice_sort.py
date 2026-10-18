"""Azure / edge voice catalog ordering (Neural before HD)."""

from __future__ import annotations

from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
    azure_voice_sort_key,
)


def test_azure_voice_sort_neural_before_hd() -> None:
    voices = [
        'en-US-Ava:DragonHDLatestNeural',
        'es-AR-ElenaNeural',
        'en-US-AvaMultilingualNeural',
        'es-MX-DaliaNeural',
    ]
    assert sorted(voices, key=azure_voice_sort_key) == [
        'es-AR-ElenaNeural',
        'es-MX-DaliaNeural',
        'en-US-AvaMultilingualNeural',
        'en-US-Ava:DragonHDLatestNeural',
    ]
