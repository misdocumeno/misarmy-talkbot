"""App-facing translate helper: builds ``LocaleContext`` from config (no cycle in gettext core)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from misarmy_talkbot.infra.config.config import locale_context

if TYPE_CHECKING:
    import discord
from misarmy_talkbot.infra.locale.translations import translate as _translate


def translate(msgid: str, guild: discord.Guild | None = None) -> str:
    return _translate(msgid, context=locale_context(guild))
