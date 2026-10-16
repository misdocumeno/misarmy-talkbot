"""Static USD estimates for ``/tts-usage`` (not live billing)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProviderCostModel(BaseModel):
    """Per-million-character list price and free monthly allowance."""

    usd_per_million_chars: float = Field(ge=0.0)
    free_chars_per_month: int = Field(default=0, ge=0)


# List prices after free tiers; gTTS and edge scrape are $0.
# Google Cloud Standard: https://cloud.google.com/text-to-speech/pricing
DEFAULT_COST_MODELS: dict[str, ProviderCostModel] = {
    'azure': ProviderCostModel(
        usd_per_million_chars=16.0, free_chars_per_month=500_000
    ),
    'edge': ProviderCostModel(
        usd_per_million_chars=0.0, free_chars_per_month=0
    ),
    'google': ProviderCostModel(
        usd_per_million_chars=0.0, free_chars_per_month=0
    ),
    # Chirp 3 HD list price after 1M free chars/month.
    'google_cloud': ProviderCostModel(
        usd_per_million_chars=30.0, free_chars_per_month=1_000_000
    ),
}


def estimate_usd(
    *,
    provider: str,
    chars: int,
    cost_models: dict[str, ProviderCostModel] | None = None,
) -> float:
    """Estimate billable USD for ``chars`` from ``provider`` (post free tier)."""
    models = cost_models or DEFAULT_COST_MODELS
    model = models.get(provider)
    if model is None or chars <= 0:
        return 0.0
    billable = max(0, chars - model.free_chars_per_month)
    return (billable / 1_000_000.0) * model.usd_per_million_chars
