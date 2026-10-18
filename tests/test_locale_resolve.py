"""Locale language extraction and catalog fallback."""

from __future__ import annotations

from misarmy_talkbot.infra.locale.resolve import (
    language_code,
    resolve_supported_locale,
)


def test_language_code() -> None:
    assert language_code('es_AR') == 'es'
    assert language_code('es-419') == 'es'
    assert language_code('en_US') == 'en'


def test_es_419_maps_to_es_ES() -> None:
    assert resolve_supported_locale('es_419') == 'es_ES'
    assert resolve_supported_locale('es-419') == 'es_ES'


def test_exact_supported_unchanged() -> None:
    assert resolve_supported_locale('es_AR') == 'es_AR'
    assert resolve_supported_locale('en_US') == 'en_US'
