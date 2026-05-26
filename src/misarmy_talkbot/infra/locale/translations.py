import gettext
import os
import shutil
import subprocess
from locale import getlocale
from pathlib import Path
from typing import cast

import discord

from misarmy_talkbot.args import args
from misarmy_talkbot.infra.locale.context import LocaleContext
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.paths import CONFIG_DIR

localedir = os.path.dirname(os.path.realpath(__file__))

_BUNDLED_LOCALES = [
    locale
    for locale in os.listdir(localedir)
    if os.path.isdir(os.path.join(localedir, locale))
    and locale != '__pycache__'
]

_CUSTOM_LOCALES_DIR = CONFIG_DIR / 'locales'


def _compile_custom_locales(locales_dir: Path) -> None:
    """Compile config-mounted ``.po`` files to ``.mo`` if needed.

    Custom locales are expected at:

        config/locales/<name>/LC_MESSAGES/messages.po

    If a ``messages.mo`` exists and is newer than the ``.po`` it is not rebuilt.
    """
    if not locales_dir.exists():
        return

    msgfmt = shutil.which('msgfmt')
    if msgfmt is None:
        logger.warning(
            'Custom locales present at %s but msgfmt was not found; skipping compilation.',
            locales_dir,
        )
        return

    for locale_dir in locales_dir.iterdir():
        if not locale_dir.is_dir():
            continue
        po_path = locale_dir / 'LC_MESSAGES' / 'messages.po'
        mo_path = locale_dir / 'LC_MESSAGES' / 'messages.mo'
        if not po_path.exists():
            continue
        if mo_path.exists() and mo_path.stat().st_mtime >= po_path.stat().st_mtime:
            continue

        mo_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [msgfmt, '-o', str(mo_path), str(po_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            logger.info('Compiled custom locale %s', locale_dir.name)
        except subprocess.CalledProcessError as exc:
            logger.error(
                'Failed to compile custom locale %s: %s',
                locale_dir.name,
                (exc.stdout or '').strip() or str(exc),
            )


def _discover_custom_locales(locales_dir: Path) -> tuple[list[str], dict[str, str]]:
    """Return custom locale codes and optional fallback map.

    If ``config/locales/<name>/fallback`` exists, its contents are treated as a
    locale code used as the translation fallback chain.
    """
    locales: list[str] = []
    fallbacks: dict[str, str] = {}

    if not locales_dir.exists():
        return locales, fallbacks

    for locale_dir in locales_dir.iterdir():
        if not locale_dir.is_dir():
            continue
        lc_messages = locale_dir / 'LC_MESSAGES'
        if not lc_messages.exists():
            continue

        has_mo = (lc_messages / 'messages.mo').exists()
        has_po = (lc_messages / 'messages.po').exists()
        if not has_mo and not has_po:
            continue

        locales.append(locale_dir.name)
        fallback_path = locale_dir / 'fallback'
        if fallback_path.exists():
            try:
                base = fallback_path.read_text(encoding='utf-8').strip()
            except OSError:
                base = ''
            if base:
                fallbacks[locale_dir.name] = base

    return locales, fallbacks


_compile_custom_locales(_CUSTOM_LOCALES_DIR)
_CUSTOM_LOCALES, _CUSTOM_FALLBACKS = _discover_custom_locales(_CUSTOM_LOCALES_DIR)

supported_locales = [*_BUNDLED_LOCALES, *_CUSTOM_LOCALES]

if not supported_locales:
    logger.critical('No locales found.')
    exit(1)

translations: dict[str, gettext.GNUTranslations] = {}

for locale in _BUNDLED_LOCALES:
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

for locale in _CUSTOM_LOCALES:
    try:
        translations[locale] = gettext.translation(
            'messages', localedir=str(_CUSTOM_LOCALES_DIR), languages=[locale]
        )
        logger.debug('Loading custom locale %r.', locale)
    except FileNotFoundError:
        # Custom locales are optional and operator-controlled; if compilation
        # failed or the file is missing, skip without bricking the bot.
        logger.warning('Custom locale %r could not be loaded.', locale)

for locale, base in _CUSTOM_FALLBACKS.items():
    if locale not in translations:
        continue
    if base not in translations:
        logger.warning(
            'Custom locale %r requested fallback %r but it is not available.',
            locale,
            base,
        )
        continue
    translations[locale].add_fallback(translations[base])


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
