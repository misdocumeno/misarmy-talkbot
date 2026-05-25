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
    ctx: discord.ApplicationContext,
    action: Literal['get', 'set', 'cancel', 'default'],
) -> None:
    """Handles the config slash command."""
    assert ctx.guild is not None and isinstance(ctx.user, discord.Member)

    await ctx.defer(ephemeral=True)

    if action == 'default':
        await ctx.respond(
            file=discord.File(
                io.BytesIO(default_config.encode('utf-8')),
                filename=f'{ctx.guild_id}.json',
            ),
            ephemeral=True,
        )
        return

    if action == 'set':
        waiting_for_config.setdefault(ctx.guild, set()).add(ctx.user)
        await ctx.respond(
            embed=discord.Embed(
                description=translate('config_cmd_set_response', ctx.guild),
                color=discord.Colour.dark_purple(),
            ),
            ephemeral=True,
        )
        return

    if action == 'cancel':
        if ctx.user in waiting_for_config.get(ctx.guild, set()):
            waiting_for_config[ctx.guild].remove(ctx.user)
            await reply_interaction(
                ctx, discord.Colour.dark_purple(), 'config_cmd_cancel_removed'
            )
        else:
            await reply_interaction(
                ctx, discord.Colour.red(), 'config_cmd_cancel_not_waiting'
            )
        return

    guild_config = await get_config_json(ctx.guild) or default_config
    await ctx.respond(
        file=discord.File(
            io.BytesIO(guild_config.encode('utf-8')),
            filename=f'{ctx.guild_id}.json',
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
