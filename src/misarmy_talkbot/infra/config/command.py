import io
import re
from typing import Literal

import discord
from pydantic_core import ValidationError

from misarmy_talkbot.infra.config.config import (
    default_config,
    get_config_json,
    update_config,
)
from misarmy_talkbot.infra.locale import UnsupportedLocaleError, translate
from misarmy_talkbot.utils import reply_interaction, reply_unsupported_locale

waiting_for_config: dict[discord.Guild, set[discord.Member]] = {}


async def config_command(
    interaction: discord.Interaction,
    action: Literal['get', 'set', 'cancel', 'default'],
) -> None:
    """Handles the config slash command."""
    assert interaction.guild is not None and isinstance(
        interaction.user, discord.Member
    )

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    if action == 'default':
        await interaction.followup.send(
            file=discord.File(
                io.BytesIO(default_config.encode('utf-8')),
                filename=f'{interaction.guild_id}.json',
            ),
            ephemeral=True,
        )
        return

    if action == 'set':
        waiting_for_config.setdefault(interaction.guild, set()).add(
            interaction.user
        )
        await interaction.followup.send(
            embed=discord.Embed(
                description=translate(
                    'config_cmd_set_response', interaction.guild
                ),
                color=discord.Colour.dark_purple(),
            ),
            ephemeral=True,
        )
        return

    if action == 'cancel':
        if interaction.user in waiting_for_config.get(
            interaction.guild, set()
        ):
            waiting_for_config[interaction.guild].remove(interaction.user)
            await reply_interaction(
                interaction,
                discord.Colour.dark_purple(),
                'config_cmd_cancel_removed',
            )
        else:
            await reply_interaction(
                interaction,
                discord.Colour.red(),
                'config_cmd_cancel_not_waiting',
            )
        return

    guild_config = await get_config_json(interaction.guild) or default_config
    await interaction.followup.send(
        file=discord.File(
            io.BytesIO(guild_config.encode('utf-8')),
            filename=f'{interaction.guild_id}.json',
        ),
        ephemeral=True,
    )


async def on_config_mention(message: discord.Message) -> bool:
    """
    Updates the guild config with the given json by a server admin.
    Returns False if the message shouldn't be handled.
    """
    assert message.guild is not None and isinstance(
        message.author, discord.Member
    )

    # check if we should handle this message
    match = re.match(
        r'<@!?\d+>\s+```.*?\n(.*)```', message.content, flags=re.DOTALL
    )
    attachment = message.attachments[0] if message.attachments else None
    json = None

    if match:
        json = match.group(1)
    elif (
        attachment
        and attachment.content_type
        and attachment.content_type.endswith('charset=utf-8')
    ):
        json = (await attachment.read()).decode('utf-8')

    if json is None:
        return False

    # we should handle it, check if we are waiting
    # for a new config from this user, and update it
    if message.author not in waiting_for_config.get(message.guild, set()):
        await message.reply(
            embed=discord.Embed(
                description=translate(
                    'config_mention_not_waiting', message.guild
                ),
                color=discord.Colour.red(),
            )
        )
        return True

    waiting_for_config[message.guild].remove(message.author)

    try:
        await update_config(json, message.guild)
        await message.reply(
            embed=discord.Embed(
                title=translate(
                    'config_cmd_set_config_updated', message.guild
                ),
                color=discord.Colour.dark_purple(),
            )
        )
    except (ValueError, ValidationError):
        await message.reply(
            embed=discord.Embed(
                description=translate(
                    'config_cmd_set_invalid_json', message.guild
                ),
                color=discord.Colour.red(),
            )
        )
    except UnsupportedLocaleError:
        await reply_unsupported_locale(message)
    return True
