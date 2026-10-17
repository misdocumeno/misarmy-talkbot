"""Google Cloud TTS billing buckets (SKU free-tier grouping).

Official pricing (shared SKUs):
  - Standard + WaveNet → same SKU, 4M free chars/month
  - Neural2 + Polyglot → same SKU, 1M free
  - Chirp 3 HD → 1M free
  - Studio → 1M free then $160/1M (excluded from our catalog)
  - Instant custom / Gemini-TTS → no free tier (unsupported)

https://cloud.google.com/text-to-speech/pricing
"""

from __future__ import annotations

from typing import Literal

GoogleBillingBucket = Literal[
    'standard_wavenet',
    'neural2',
    'chirp3',
    'studio',
    'unsupported',
]

# Default catalog (Studio opt-in via ``GOOGLE_TTS_ALLOW_STUDIO``).
_CATALOG_ALLOW_BUCKETS: frozenset[GoogleBillingBucket] = frozenset({
    'standard_wavenet',
    'neural2',
    'chirp3',
})


def classify_google_voice(voice_id: str) -> GoogleBillingBucket:
    """Map a Cloud voice short name to its free-tier billing bucket."""
    # Strip optional ``google/`` prefix from usage rows.
    name = voice_id.split('/', 1)[-1]
    lower = name.lower()
    if 'studio' in lower:
        return 'studio'
    if (
        'chirp3' in lower
        or 'chirp-hd' in lower
        or '-chirp' in lower
        or lower.endswith('chirp')
    ):
        return 'chirp3'
    if 'neural2' in lower or 'polyglot' in lower:
        return 'neural2'
    if 'wavenet' in lower or 'standard' in lower:
        return 'standard_wavenet'
    # InstantCustomVoice, Gemini, Journey, etc.
    return 'unsupported'


def voice_allowed_in_catalog(
    voice_id: str,
    *,
    voice_tier: str,
    allow_studio: bool = False,
) -> bool:
    """Whether ``voice_id`` should appear in ``/voices`` for the configured tier."""
    tier = voice_tier.strip().lower() or 'all'
    bucket = classify_google_voice(voice_id)
    allowed = set(_CATALOG_ALLOW_BUCKETS)
    if allow_studio:
        allowed.add('studio')
    if bucket not in allowed:
        return False
    if tier == 'all':
        return True
    if tier == 'studio':
        return bucket == 'studio'
    # Substring filter (Chirp3, Standard, WaveNet, Neural2).
    return tier in voice_id.lower()


def default_cap_for_bucket(bucket: GoogleBillingBucket) -> int:
    """Official recurring free allotment for ``bucket`` (0 = do not use)."""
    if bucket == 'standard_wavenet':
        return 4_000_000
    if bucket in {'neural2', 'chirp3', 'studio'}:
        return 1_000_000
    return 0
