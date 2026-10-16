"""TTS registry routing and prosody validation."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from misarmy_talkbot.core.playback.tts.exceptions import ProsodyValidationError
from misarmy_talkbot.core.playback.tts.providers.edge_backend import (
    EdgeTtsProvider,
)
from misarmy_talkbot.core.playback.tts.providers.google_backend import (
    GoogleTtsProvider,
)
from misarmy_talkbot.core.playback.tts.registry import TtsRegistry
from misarmy_talkbot.infra.database.tts_usage import TtsUsageStore

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from misarmy_talkbot.core.playback.tts.provider import TtsProvider


@pytest.fixture(autouse=True)
def _reset_registry() -> Generator[None, None, None]:
    TtsRegistry.reset()
    TtsUsageStore.reset()
    yield
    TtsRegistry.reset()
    TtsUsageStore.reset()


def test_build_default_without_azure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('AZURE_SPEECH_KEY', raising=False)
    monkeypatch.delenv('AZURE_SPEECH_REGION', raising=False)
    monkeypatch.delenv('GOOGLE_TTS_API_KEY', raising=False)
    store = TtsUsageStore(jsonl_dir=tmp_path / 'tts-usage')
    TtsUsageStore.set_instance(store)
    registry = TtsRegistry.build_default(store)
    assert registry.has('edge')
    assert registry.has('google')
    assert isinstance(registry.get('edge'), EdgeTtsProvider)
    assert isinstance(registry.get('google'), GoogleTtsProvider)


def test_build_default_with_google_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('AZURE_SPEECH_KEY', raising=False)
    monkeypatch.delenv('AZURE_SPEECH_REGION', raising=False)
    monkeypatch.setenv('GOOGLE_TTS_API_KEY', 'test-key')
    from misarmy_talkbot.core.playback.tts.providers.google_router import (
        GoogleTtsRouter,
    )

    store = TtsUsageStore(jsonl_dir=tmp_path / 'tts-usage')
    TtsUsageStore.set_instance(store)
    registry = TtsRegistry.build_default(store)
    assert isinstance(registry.get('google'), GoogleTtsRouter)


def test_parse_and_validate_prosody() -> None:
    registry = TtsRegistry({
        'edge': cast('TtsProvider', EdgeTtsProvider()),
        'google': cast('TtsProvider', GoogleTtsProvider()),
    })
    TtsRegistry.set_instance(registry)
    assert registry.parse_voice('edge/es-AR-ElenaNeural') == (
        'edge',
        'es-AR-ElenaNeural',
    )
    registry.validate_prosody('edge/es-AR-ElenaNeural', pitch=1.0, speed=1.0)
    # Azure SSML window: pitch outside 0.5–1.5 is rejected for edge/.
    with pytest.raises(ProsodyValidationError):
        registry.validate_prosody(
            'edge/es-AR-ElenaNeural', pitch=0.1, speed=1.0
        )
    # gTTS+ffmpeg: outside Azure window is still OK if positive.
    registry.validate_prosody('google/es-Spanish', pitch=2.0, speed=0.5)
    with pytest.raises(ProsodyValidationError):
        registry.validate_prosody('google/es-Spanish', pitch=0.0, speed=1.0)


def test_unknown_prefix() -> None:
    registry = TtsRegistry({
        'google': cast('TtsProvider', GoogleTtsProvider())
    })
    with pytest.raises(ValueError, match='unsupported'):
        registry.get('edge')
