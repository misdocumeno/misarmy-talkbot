"""TTS providers, registry, and synthesis helpers."""

from misarmy_talkbot.core.playback.tts.registry import TtsRegistry
from misarmy_talkbot.core.playback.tts.synthesize import synthesize_mp3

__all__ = ['TtsRegistry', 'synthesize_mp3']
