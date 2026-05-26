"""Locale resolution inputs for gettext (no config imports)."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LocaleContext:
    """Resolved locale and per-guild string overrides for one lookup."""

    locale: str
    overrides: dict[str, str]
