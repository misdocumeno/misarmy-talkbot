from misarmy_talkbot.infra.locale.context import LocaleContext
from misarmy_talkbot.infra.locale.translations import (
    UnsupportedLocaleError,
    global_locale,
    is_supported_locale,
    supported_locales,
    validate_locale,
)

__all__ = [
    'LocaleContext',
    'UnsupportedLocaleError',
    'global_locale',
    'is_supported_locale',
    'supported_locales',
    'translate',
    'validate_locale',
]


def __getattr__(name: str) -> object:
    if name == 'translate':
        from misarmy_talkbot.infra.locale.i18n import translate

        return translate
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
