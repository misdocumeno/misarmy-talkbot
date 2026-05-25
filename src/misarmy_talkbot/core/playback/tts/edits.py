import discord
import regex

from misarmy_talkbot.infra.config.config import config, global_config
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.utils import get_emoji_name


def apply_edits(message: discord.Message) -> str:
    assert message.guild is not None
    edited = message.content.lower()
    global_replace = global_config.replacements
    guild_replace = config[message.guild].replacements

    all_edits = 0

    for _ in range(100):
        edits = 0

        edited, count = replace_emojis(
            edited,
            message.guild,
            {**global_replace.emojis, **guild_replace.emojis},
        )
        edits += count
        edited, count = replace_stickers(
            edited,
            message,
            {**global_replace.stickers, **guild_replace.stickers},
        )
        edits += count
        edited, count = replace_mentions(
            edited,
            message,
            {**global_replace.mentions, **guild_replace.mentions},
        )
        edits += count
        edited, count = replace_roles(
            edited, message, {**global_replace.roles, **guild_replace.roles}
        )
        edits += count
        edited, count = replace_channels(
            edited,
            message,
            {**global_replace.channels, **guild_replace.channels},
        )
        edits += count
        edited, count = replace_regex(
            edited, {**global_replace.regex, **guild_replace.regex}
        )
        edits += count

        all_edits += edits

        if edits == 0:
            break

    if all_edits > 0:
        logger.debug(
            f'Applied {all_edits} edits from {message.content!r} to {edited!r}'
        )
    else:
        logger.debug(f'Applied 0 edits to {message.content!r}')

    return edited


def replace_regex(text: str, replacements: dict[str, str]) -> tuple[str, int]:
    edits = 0
    for pattern, replacement in replacements.items():
        text, count = regex.subn(
            pattern, replacement, text, flags=regex.DOTALL
        )
        edits += count
    return text, edits


def replace_emojis(
    text: str, guild: discord.Guild, replacements: dict[str, str]
) -> tuple[str, int]:
    edits = 0
    emojis = regex.findall(r'(<a?:(\w+):(\d+)>)', text)
    emojis.extend(
        regex.findall(
            r'(\[(.+?)\]\(https://cdn.discordapp.com/emojis/(\d+).+?\))', text
        )
    )

    text = regex.sub(r'(<a?:(\w+):(\d+)>)(?!$|\s*\.\s*)', r'\1.', text)
    text = regex.sub(r'(?<!^|\s*\.\s*)(<a?:(\w+):(\d+)>)', r'. \1', text)
    text = regex.sub(
        r'(\[(.+?)\]\(https://cdn.discordapp.com/emojis/(\d+).+?\))(?!$|\s*\.\s*)',
        r'\1.',
        text,
    )
    text = regex.sub(
        r'(?<!^|\s*\.\s*)(\[(.+?)\]\(https://cdn.discordapp.com/emojis/(\d+).+?\))',
        r'. \1',
        text,
    )

    for emoji in emojis:
        name = get_emoji_name(guild, int(emoji[2])) or emoji[1]
        edits += text.count(emoji[0])

        if emoji[2] in replacements:
            replacement = replacements[emoji[2]]
        elif name in replacements:
            replacement = replacements[name]
        else:
            replacement = regex.sub(r'~\d+$', '', name)
            replacement = regex.sub(r'[-_]+', ' ', replacement)

        text = text.replace(emoji[0], replacement)

    return text, edits


def replace_stickers(
    text: str, message: discord.Message, replacements: dict[str, str]
) -> tuple[str, int]:
    edits = 0
    stickers = message.stickers + regex.findall(
        r'(\[(.+?)\]\(https://media.discordapp.net/stickers/(\d+).+?\))', text
    )

    for sticker in stickers:
        edits += 1
        if isinstance(sticker, tuple):
            sticker_id = sticker[2]
            sticker_name = sticker[1]
        else:
            sticker_id = sticker.id
            sticker_name = sticker.name

        if str(sticker_id) in replacements:
            replacement = replacements[str(sticker_id)]
        elif sticker_name in replacements:
            replacement = replacements[sticker_name]
        else:
            replacement = regex.sub(r'~\d+$', '', sticker_name)
            replacement = regex.sub(r'[-_]+', ' ', replacement)

        if isinstance(sticker, tuple):
            text = text.replace(sticker[0], replacement)
        else:
            text += replacement

    message.stickers.clear()
    return text, edits


def replace_mentions(
    text: str, message: discord.Message, replacements: dict[str, str]
) -> tuple[str, int]:
    edits = 0
    for mention in message.mentions:
        if str(mention.id) in replacements:
            member = replacements[str(mention.id)]
        else:
            member = mention.display_name
        text, count = regex.subn(f'<@!?{mention.id}>', member, text)
        edits += count
    return text, edits


def replace_roles(
    text: str, message: discord.Message, replacements: dict[str, str]
) -> tuple[str, int]:
    edits = 0
    for mention in message.role_mentions:
        if str(mention.id) in replacements:
            role = replacements[str(mention.id)]
        else:
            role = mention.name
        text, count = regex.subn(f'<@&{mention.id}>', role, text)
        edits += count
    return text, edits


def replace_channels(
    text: str, message: discord.Message, replacements: dict[str, str]
) -> tuple[str, int]:
    edits = 0
    for mention in message.channel_mentions:
        if str(mention.id) in replacements:
            channel = replacements[str(mention.id)]
        else:
            channel = mention.name
        text, count = regex.subn(f'<#{mention.id}>', channel, text)
        edits += count
    return text, edits
