import gettext
import os
from locale import getlocale
from typing import cast

import discord

from misarmy_talkbot.args import args
from misarmy_talkbot.infra.locale.context import LocaleContext
from misarmy_talkbot.observability.logger import logger

localedir = os.path.dirname(os.path.realpath(__file__))

supported_locales = [
    locale
    for locale in os.listdir(localedir)
    if os.path.isdir(os.path.join(localedir, locale))
    and locale != '__pycache__'
]

if not supported_locales:
    logger.critical('No locales found.')
    exit(1)

translations: dict[str, gettext.GNUTranslations] = {}

for locale in supported_locales:
    try:
        translations[locale] = gettext.translation(
            'messages', localedir=localedir, languages=[locale]
        )
        logger.debug(f'Loading locale {locale!r}.')
    except FileNotFoundError as e:
        logger.critical(
            f'Failed to load locale {locale!r}, try running misarmy_talkbot/infra/locale/compile.py'
        )
        exit(e.errno)


if args.locale is not None:
    global_locale = cast('str', args.locale)
elif 'LANG' in os.environ:
    global_locale = os.environ['LANG'].split('.')[0]
elif getlocale()[0] is not None:
    global_locale = cast('str', getlocale()[0]).split('.')[0]
else:
    global_locale = (
        'en_US' if 'en_US' in supported_locales else supported_locales[0]
    )

# WSL/minimal images often report LANG=C or POSIX; treat as missing locale.
if global_locale in ('C', 'POSIX'):
    global_locale = (
        'en_US' if 'en_US' in supported_locales else supported_locales[0]
    )

if global_locale not in supported_locales:
    logger.warning(
        'Unsupported global locale %r, falling back to %r.',
        global_locale,
        'en_US' if 'en_US' in supported_locales else supported_locales[0],
    )
    global_locale = (
        'en_US' if 'en_US' in supported_locales else supported_locales[0]
    )

logger.info(f'Using global locale {global_locale!r}.')


def translate(msgid: str, *, context: LocaleContext) -> str:
    """Resolve ``msgid`` using injected locale state (see ``infra.locale.i18n``)."""
    if msgid in context.overrides:
        return context.overrides[msgid]
    return translations[context.locale].gettext(msgid)


class UnsupportedLocaleError(Exception):
    pass


def validate_locale(locale: str, guild: discord.Guild | None) -> None:
    if guild is not None:
        if locale not in supported_locales:
            raise UnsupportedLocaleError(locale)
        return

    if locale not in (global_locale, 'guild_preferred'):
        logger.warning(
            f'Global config locale {locale!r} was ignored and it differs from {global_locale!r}. '
            'Set the global locale with --locale or setting the LANG environment variable.'
        )


def is_supported_locale(locale: str) -> bool:
    return locale in supported_locales
