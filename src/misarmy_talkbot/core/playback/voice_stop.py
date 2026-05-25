"""Safe VoiceClient.stop() — discord/ffmpeg can raise on already-closed pipes."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from discord.voice import VoiceClient


def safe_voice_stop(
    voice_client: VoiceClient | None,
    *,
    reason: str = 'unspecified',
) -> None:
    if voice_client is None:
        return
    channel = voice_client.channel
    guild = getattr(channel, 'guild', None)
    guild_id = guild.id if guild is not None else None
    logger.debug(
        'voice_playback_stop guild_id=%s reason=%s initiated_by=us',
        guild_id,
        reason,
    )
    with suppress(ValueError, OSError):
        voice_client.stop()
