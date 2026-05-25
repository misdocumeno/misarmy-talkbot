"""Rate-limited operational replies when TTS or playback fails for a user message."""

from __future__ import annotations

import os
import time
from typing import Literal

import discord

from misarmy_talkbot.infra.locale import translate
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry

Phase = Literal['tts', 'voice', 'ffmpeg', 'playback', 'permissions']


class ErrorReplyAnnouncer:
    """Send a single embed reply per cooldown window when automation fails mid-message.

    Without throttling, a broken voice pipeline would spam every failing message; operators
    asked for a quiet failure mode that still tells the user what phase broke.
    """

    def __init__(self, guild_id: int) -> None:
        self.guild_id = guild_id
        self._cooldown_until: dict[int, float] = {}
        self._cooldown_s = float(
            os.getenv('OPS_ANNOUNCE_COOLDOWN_SECONDS', '300')
        )

    def reset_cooldown(self, user_id: int) -> None:
        self._cooldown_until.pop(user_id, None)

    async def announce(
        self, message: discord.Message, phase: Phase, summary: str
    ) -> None:
        author_user_id = message.author.id
        now = time.monotonic()
        cooldown_end = self._cooldown_until.get(author_user_id, 0.0)
        if now < cooldown_end:
            logger.debug(
                'ops_announce_suppressed guild_id=%s user_id=%s phase=%s',
                self.guild_id,
                author_user_id,
                phase,
            )
            return
        channel = message.channel
        bot_member = message.guild.me if message.guild else None
        if bot_member is not None and isinstance(
            channel, discord.abc.GuildChannel
        ):
            if not channel.permissions_for(bot_member).send_messages:
                logger.warning(
                    'ops_announce_no_send_perms guild_id=%s channel_id=%s',
                    self.guild_id,
                    channel.id,
                )
                return

        phase_msgids = {
            'tts': 'ops_announce_phase_tts',
            'voice': 'ops_announce_phase_voice',
            'ffmpeg': 'ops_announce_phase_ffmpeg',
            'playback': 'ops_announce_phase_playback',
            'permissions': 'ops_announce_phase_permissions',
        }
        guild = message.guild
        phase_label = (
            translate(phase_msgids[phase], guild)
            if phase in phase_msgids
            else phase
        )
        embed = discord.Embed(
            title=translate('ops_announce_title', guild),
            description=f'{phase_label}\n{summary}',
            color=discord.Colour.red(),
        )
        try:
            await message.reply(embed=embed, mention_author=False)
        except discord.HTTPException as error:
            logger.warning(
                'ops_announce_http_fail guild_id=%s err=%s',
                self.guild_id,
                error,
            )
            return
        self._cooldown_until[author_user_id] = now + self._cooldown_s
        MetricsRegistry.instance().inc('op_announce_total', self.guild_id)
