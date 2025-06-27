import gtts
import gtts.lang
import edge_tts
import discord
from typing import cast
from .database.preset import get_presets
from .locale.translations import translate
from .args import args


async def reply_interaction(
    interaction: discord.Interaction,
    color: discord.Colour,
    msgid: str,
    footer_msgid='',
    **kwargs
):
    """Reply to an interaction with an embed with a translated message, formatting it with the given kwargs."""
    embed = discord.Embed(color=color, title=translate(msgid, interaction.guild).format(**kwargs))
    embed.set_footer(text=translate(footer_msgid, interaction.guild).format(**kwargs) if footer_msgid != '' else None)
    await interaction.response.send_message(embed=embed, ephemeral=True)


def get_emoji_name(guild: discord.Guild, emoji_id: int) -> str | None:
    """
    Get the name of an emoji from its id, with the tilde and number,
    in case there are more than one with the same name.
    """
    emoji = guild.get_emoji(emoji_id)
    if emoji is None:
        return None
    emojis = [e for e in guild.emojis if e.name == emoji.name]
    index = emojis.index(emoji) + 1
    return f'{emoji.name}~{index}' if index > 1 else emoji.name


async def reply_link_embed(interaction: discord.Interaction, client: discord.Client):
    if interaction.client.user is None:
        return

    avatar = interaction.client.user.avatar.url if interaction.client.user and interaction.client.user.avatar else None
    bot_invite = ('https://discord.com/api/oauth2/authorize?client_id='
                  f'{interaction.client.user.id}&permissions=0&scope=bot')

    server_invite = client.get_guild(args.invite_guild)
    assert server_invite is not None
    invite = await (server_invite.rules_channel or server_invite.text_channels[0]).create_invite(max_age=300)

    embed = discord.Embed(
        title=translate('invite_title', interaction.guild),
        description=translate('invite_description', interaction.guild),
        color=discord.Colour.dark_purple())
    embed.set_thumbnail(url=avatar)

    view = discord.ui.View()
    view.add_item(discord.ui.Button(label=translate('bot_invite_button', interaction.guild), url=bot_invite))
    view.add_item(discord.ui.Button(label=translate('server_invite_button', interaction.guild), url=invite.url))

    await interaction.response.send_message(embed=embed, view=view)


async def reply_unsupported_locale(message: discord.Message):
    repo = 'https://github.com/misdocumeno/misarmy-talkbot'
    overrides = f'{repo}/?tab=readme-ov-file#overrides'
    description = translate('config_cmd_set_invalid_locale', message.guild).format(repo=repo, overrides=overrides)
    embed = discord.Embed(description=description, color=discord.Colour.red())
    embed.set_footer(text=translate('config_cmd_set_invalid_locale_footer', message.guild))
    await message.reply(embed=embed)


async def voices_list() -> list[str]:
    return [
        *[f'google/{code}-{country}' for code, country in gtts.lang.tts_langs().items()],
        *[f'edge/{voice['ShortName']}' for voice in await edge_tts.list_voices()]
    ]


async def voice_choices(_: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    return [
        discord.app_commands.Choice(name=voice, value=voice)
        for voice in await voices_list() if current.lower() in voice.lower()
    ][:25]


async def preset_choices(interaction: discord.Interaction, current: str) -> list[discord.app_commands.Choice[str]]:
    presets = await get_presets(cast(discord.Member, interaction.user))
    return [
        discord.app_commands.Choice(name=preset.name, value=preset.name)
        for preset in presets if current.lower() in preset.name.lower()
    ][:25]


def is_deafened(member: discord.Member) -> bool:
    return member.voice is not None and (member.voice.deaf or member.voice.self_deaf)
