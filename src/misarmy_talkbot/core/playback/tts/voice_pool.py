"""Cheap auto-assign voice pools keyed by language (not region)."""

from __future__ import annotations

from misarmy_talkbot.core.playback.tts.google_billing import (
    classify_google_voice,
)
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.utils import voices_list

# Mild pitch band for auto-assign (within Microsoft 0.5–1.5).
AUTO_PITCH_MIN = 0.85
AUTO_PITCH_MAX = 1.15

# Google: 4M (Standard/WaveNet) + 1M Neural2. Chirp/Studio excluded.
_GOOGLE_AUTO_BUCKETS = frozenset({'standard_wavenet', 'neural2'})


def _short_name(full_voice_id: str) -> str:
    return full_voice_id.split('/', 1)[-1]


def voice_language(full_voice_id: str) -> str:
    """``ms/es-AR-ElenaNeural`` / ``google/es-US-Standard-A`` → ``es``."""
    short = _short_name(full_voice_id)
    return short.split('-', 1)[0].lower()


def is_ms_auto_assign_voice(short_name: str) -> bool:
    """Azure F0 neural grant (~500k chars); exclude HD / DragonHD SKUs."""
    lower = short_name.lower()
    if 'dragonhd' in lower or 'neuralhd' in lower or '-hd-' in lower:
        return False
    return 'neural' in lower


def is_google_auto_assign_voice(short_name: str) -> bool:
    return classify_google_voice(short_name) in _GOOGLE_AUTO_BUCKETS


def is_auto_assign_voice(full_voice_id: str) -> bool:
    prefix, _, short = full_voice_id.partition('/')
    if not short:
        return False
    if prefix == 'ms':
        return is_ms_auto_assign_voice(short)
    if prefix == 'google':
        return is_google_auto_assign_voice(short)
    return False


async def cheap_pool_for_language(language: str) -> list[str]:
    """Stable-sorted full voice ids for ``language`` (e.g. ``es``)."""
    lang = language.lower()
    pool = [
        voice
        for voice in await voices_list()
        if is_auto_assign_voice(voice) and voice_language(voice) == lang
    ]
    pool.sort()
    if not pool:
        logger.warning('cheap_voice_pool_empty language=%s', lang)
    return pool


async def next_auto_voice_for_guild(guild_id: int, language: str) -> str:
    """Next circular pool entry for ``guild_id`` (bumps rotation cursor)."""
    from misarmy_talkbot.infra.database.voice_rotation import (
        take_next_rotation_index,
    )

    pool = await cheap_pool_for_language(language)
    if not pool and language != 'en':
        pool = await cheap_pool_for_language('en')
    if not pool:
        # Last resort: any auto-assign voice from the registry.
        pool = [
            voice
            for voice in await voices_list()
            if is_auto_assign_voice(voice)
        ]
        pool.sort()
    if not pool:
        raise RuntimeError('no auto-assign voices available')

    index = await take_next_rotation_index(guild_id)
    return pool[index % len(pool)]


def random_auto_pitch() -> float:
    import random

    return round(random.uniform(AUTO_PITCH_MIN, AUTO_PITCH_MAX), 2)


def guild_language_for_pool(guild_locale: str) -> str:
    """Effective TTS pool language from resolved guild config locale."""
    from misarmy_talkbot.infra.locale.resolve import language_code

    return language_code(guild_locale)
