"""Resolve Discord / config locale tags to a supported catalog locale."""

from __future__ import annotations

from misarmy_talkbot.infra.locale.translations import (
    global_locale,
    supported_locales,
)

# Discord tags we do not ship as folders → nearest bundled locale.
_LOCALE_ALIASES: dict[str, str] = {
    'es_419': 'es_ES',
}


def language_code(locale: str) -> str:
    """``es_AR`` / ``es-419`` / ``en_US`` → ``es`` / ``es`` / ``en``."""
    return locale.replace('-', '_').split('_', 1)[0].lower()


def normalize_locale_tag(locale: str) -> str:
    return locale.replace('-', '_')


def resolve_supported_locale(requested: str) -> str:
    """Map a requested locale to one we ship.

    Order: exact match → alias (``es_419``→``es_ES``) → any same language
    (prefer ``es_ES`` then ``es_AR`` for Spanish) → global locale.
    """
    norm = normalize_locale_tag(requested)
    if norm in supported_locales:
        return norm

    alias = _LOCALE_ALIASES.get(norm)
    if alias is not None and alias in supported_locales:
        return alias

    lang = language_code(norm)
    same_lang = [
        loc for loc in supported_locales if language_code(loc) == lang
    ]
    if lang == 'es':
        for preferred in ('es_ES', 'es_AR'):
            if preferred in same_lang:
                return preferred
    if same_lang:
        return sorted(same_lang)[0]
    return global_locale
