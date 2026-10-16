"""Maps user-facing voice prefixes (``edge``, ``google``) to TTS backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.core.playback.tts.exceptions import ProsodyValidationError
from misarmy_talkbot.core.playback.tts.providers.edge_backend import (
    EdgeTtsProvider,
)
from misarmy_talkbot.core.playback.tts.providers.google_backend import (
    GoogleTtsProvider,
)
from misarmy_talkbot.core.playback.tts.settings import (
    load_azure_speech_settings,
)
from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from misarmy_talkbot.core.playback.tts.provider import (
        ProsodyLimits,
        TtsProvider,
    )
    from misarmy_talkbot.infra.database.tts_usage import TtsUsageStore


class TtsRegistry:
    """Maps user-facing voice prefixes to concrete ``TtsProvider`` backends.

    Built once at bot startup via ``build_default``. Unit tests call ``reset`` /
    ``set_instance`` so each test gets an isolated registry without sharing
    process-wide Azure/edge wiring from a previous case.
    """

    _instance: TtsRegistry | None = None

    def __init__(self, providers: dict[str, TtsProvider]) -> None:
        self._providers = providers

    @classmethod
    def instance(cls) -> TtsRegistry:
        if cls._instance is None:
            raise RuntimeError(
                'TtsRegistry not initialized; call build_default() first'
            )
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear the process singleton (tests only)."""
        cls._instance = None

    @classmethod
    def set_instance(cls, registry: TtsRegistry) -> None:
        """Install a registry built by tests or alternate composition roots."""
        cls._instance = registry

    @classmethod
    def build_default(cls, usage_store: TtsUsageStore) -> TtsRegistry:
        """Wire Google + Microsoft backends from env.

        Azure / Google Cloud imports stay inside ``if`` so a bot without those
        credentials never loads the paid modules.
        """
        from misarmy_talkbot.core.playback.tts.settings import (
            load_google_cloud_tts_settings,
        )

        gtts_provider = GoogleTtsProvider()
        google_settings = load_google_cloud_tts_settings()
        providers: dict[str, TtsProvider] = {}
        if google_settings.enabled:
            from misarmy_talkbot.core.playback.tts.providers.google_cloud_backend import (
                GoogleCloudTtsProvider,
            )
            from misarmy_talkbot.core.playback.tts.providers.google_router import (
                GoogleTtsRouter,
            )

            providers['google'] = GoogleTtsRouter(
                cloud=GoogleCloudTtsProvider(google_settings),
                gtts=gtts_provider,
            )
            logger.info(
                'tts_registry google_cloud_enabled tier=%s',
                google_settings.voice_tier,
            )
        else:
            providers['google'] = gtts_provider
            logger.info('tts_registry google_cloud_disabled using_gtts')

        azure_settings = load_azure_speech_settings()
        edge_provider = EdgeTtsProvider()
        if azure_settings.enabled:
            from misarmy_talkbot.core.playback.tts.providers.azure_backend import (
                AzureTtsProvider,
            )
            from misarmy_talkbot.core.playback.tts.providers.microsoft_router import (
                MicrosoftTtsRouter,
            )

            azure_provider = AzureTtsProvider(azure_settings)
            providers['edge'] = MicrosoftTtsRouter(
                azure=azure_provider,
                edge=edge_provider,
                settings=azure_settings,
                usage_store=usage_store,
            )
            logger.info(
                'tts_registry azure_enabled region=%s monthly_cap=%s',
                azure_settings.region,
                azure_settings.monthly_cap,
            )
        else:
            providers['edge'] = edge_provider
            logger.info('tts_registry azure_disabled using_edge_tts')

        registry = cls(providers)
        cls._instance = registry
        return registry

    def get(self, prefix: str) -> TtsProvider:
        try:
            return self._providers[prefix]
        except KeyError as exc:
            raise ValueError(f'unsupported TTS provider: {prefix!r}') from exc

    def has(self, prefix: str) -> bool:
        return prefix in self._providers

    def prefixes(self) -> list[str]:
        return sorted(self._providers)

    def parse_voice(self, voice: str) -> tuple[str, str]:
        """Split ``edge/es-AR-ElenaNeural`` into ``(edge, es-AR-ElenaNeural)``."""
        prefix, separator, voice_id = voice.partition('/')
        if not separator or not prefix or not voice_id:
            raise ValueError(f'invalid voice id: {voice!r}')
        return prefix, voice_id

    def prosody_limits_for_voice(self, voice: str) -> ProsodyLimits:
        prefix, _voice_id = self.parse_voice(voice)
        return self.get(prefix).prosody_limits

    def validate_prosody(
        self, voice: str, *, pitch: float, speed: float
    ) -> None:
        """Raise ``ProsodyValidationError`` if pitch/speed are out of range."""
        try:
            limits = self.prosody_limits_for_voice(voice)
            limits.validate_values(pitch=pitch, speed=speed)
        except ValueError as exc:
            raise ProsodyValidationError(str(exc)) from exc

    def backend_name_for(self, prefix: str) -> str:
        provider = self.get(prefix)
        last = getattr(provider, 'last_backend_name', None)
        if isinstance(last, str):
            return last
        return provider.backend_name
