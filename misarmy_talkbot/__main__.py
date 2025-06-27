import os
import sys
import signal
import discord
from typing import Literal, Any, cast
from pydantic_core import ValidationError
from dotenv import load_dotenv
from .logger import logger
from .tts.reader import GuildReader
from .locale.translations import translate, supported_locales, UnsupportedLocaleError
from .utils import reply_interaction, reply_link_embed, voice_choices, preset_choices
from .config.config import update_config, get_config_json, set_default_config, default_config
from .config.command import config_command, on_config_mention
from .database.database import create_tables
from .database.voice import get_voice, update_voice
from .database.preset import get_preset, get_presets, save_preset, delete_preset
from .args import args


# TODO: add checks of readers[guild] and anything else that is accessed by guild

load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

readers: dict[discord.Guild, GuildReader] = {}


async def init_guild(guild: discord.Guild | None = None) -> GuildReader | None:
    """
    Loads a guild config, inits it's messages reader, and syncs slash commands, if the tree is provided.
    If guild is None, it just loads the global config.
    """
    try:
        # the default config should never throw tho
        await update_config(await get_config_json(guild) or default_config, guild)
    except (ValueError, ValidationError):
        await set_default_config(guild)
        text = 'global config' if guild is None else f'config for {guild.name} ({guild.id})'
        logger.error(f'Failed to load {text}, using default config instead.')

    if guild is not None:
        return GuildReader(guild)


@client.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f'Joined {guild.name} ({guild.id})')
    reader = await init_guild(guild)
    assert reader is not None
    readers[guild] = reader


@client.event
async def on_guild_remove(guild: discord.Guild):
    logger.info(f'Left {guild.name} ({guild.id})')
    del readers[guild]


@tree.command(name='follow', description=translate('follow_cmd_description'))
@discord.app_commands.guild_only
async def follow(interaction: discord.Interaction):
    if interaction.guild is not None and interaction.guild in readers and isinstance(interaction.user, discord.Member):
        await reply_interaction(interaction, *await readers[interaction.guild].follow(interaction.user))


@tree.command(name='unfollow', description=translate('unfollow_cmd_description'))
@discord.app_commands.guild_only
async def unfollow(interaction: discord.Interaction):
    if interaction.guild is not None and interaction.guild in readers and isinstance(interaction.user, discord.Member):
        await reply_interaction(interaction, *await readers[interaction.guild].unfollow(interaction.user))


@tree.command(name='ignore', description=translate('ignore_cmd_description'))
@discord.app_commands.guild_only
async def ignore(interaction: discord.Interaction):
    if (interaction.guild is not None and interaction.guild in readers
        and isinstance(interaction.user, discord.Member)
            and isinstance(interaction.channel, discord.abc.Messageable)):
        await reply_interaction(interaction, *readers[interaction.guild].ignore(interaction.user, interaction.channel))


@tree.command(name='stop', description=translate('stop_cmd_description'))
@discord.app_commands.guild_only
async def stop(interaction: discord.Interaction):
    if interaction.guild is not None and interaction.guild in readers and isinstance(interaction.user, discord.Member):
        await reply_interaction(interaction, *await readers[interaction.guild].speaker.stop(interaction.user))


@tree.command(name='stopall', description=translate('stop_all_cmd_description'))
@discord.app_commands.checks.has_permissions(mute_members=True)
@discord.app_commands.guild_only
async def stopall(interaction: discord.Interaction):
    if interaction.guild is not None and interaction.guild in readers:
        await reply_interaction(interaction, *await readers[interaction.guild].speaker.stop_all())


@tree.command(name='skip', description=translate('skip_cmd_description'))
@discord.app_commands.guild_only
async def skip(interaction: discord.Interaction):
    if interaction.guild is not None and interaction.guild in readers and isinstance(interaction.user, discord.Member):
        await reply_interaction(interaction, *await readers[interaction.guild].speaker.skip(interaction.user))


@tree.command(name='mute', description=translate('mute_cmd_description'))
@discord.app_commands.describe(target=translate('mute_cmd_target_argument_description'))
@discord.app_commands.checks.has_permissions(mute_members=True)
@discord.app_commands.guild_only
async def mute(interaction: discord.Interaction, target: discord.Member):
    if interaction.guild is not None and interaction.guild in readers:
        await reply_interaction(interaction, *await readers[interaction.guild].speaker.stop(target))


@tree.command(name='voice', description=translate('voice_cmd_description'))
@discord.app_commands.describe(new_voice=translate('voice_cmd_new_voice_argument_description'))
@discord.app_commands.autocomplete(new_voice=voice_choices)
@discord.app_commands.guild_only
async def voice(interaction: discord.Interaction, new_voice: str | None = None):
    color = discord.Colour.dark_purple()
    voice = await get_voice(cast(discord.Member, interaction.user))
    if new_voice is None:
        await reply_interaction(interaction, color, 'voice_cmd_current_voice', voice=voice.voice)
    elif await update_voice(cast(discord.Member, interaction.user), voice=new_voice):
        await reply_interaction(
            interaction, color, 'voice_cmd_new_voice',
            'voice_cmd_new_voice_footer' if voice.speed != 1.0 or voice.pitch != 1.0 else '',
            voice=new_voice)
    else:
        await reply_interaction(interaction, color, 'voice_cmd_same_voice')


@tree.command(name='pitch', description=translate('pitch_cmd_description'))
@discord.app_commands.describe(new_pitch=translate('pitch_cmd_pitch_argument_description'))
@discord.app_commands.guild_only
async def pitch(interaction: discord.Interaction, new_pitch: float | None = None):
    color = discord.Colour.dark_purple()
    if new_pitch is None:
        voice = await get_voice(cast(discord.Member, interaction.user))
        await reply_interaction(interaction, color, 'pitch_cmd_current_pitch', pitch=voice.pitch)
    elif await update_voice(cast(discord.Member, interaction.user), pitch=new_pitch):
        await reply_interaction(interaction, color, 'pitch_cmd_new_pitch', pitch=new_pitch)
    else:
        await reply_interaction(interaction, color, 'pitch_cmd_same_pitch')


@tree.command(name='speed', description=translate('speed_cmd_description'))
@discord.app_commands.describe(new_speed=translate('speed_cmd_speed_argument_description'))
@discord.app_commands.guild_only
async def speed(interaction: discord.Interaction, new_speed: float | None = None):
    color = discord.Colour.dark_purple()
    if new_speed is None:
        voice = await get_voice(cast(discord.Member, interaction.user))
        await reply_interaction(interaction, color, 'speed_cmd_current_speed', speed=voice.speed)
    elif await update_voice(cast(discord.Member, interaction.user), speed=new_speed):
        await reply_interaction(interaction, color, 'speed_cmd_new_speed', speed=new_speed)
    else:
        await reply_interaction(interaction, color, 'speed_cmd_same_speed')


@tree.command(name='preset', description=translate('preset_cmd_description'))
@discord.app_commands.describe(
    action=translate('preset_cmd_action_argument_description'),
    preset_name=translate('preset_cmd_preset_name_argument_description'))
@discord.app_commands.autocomplete(preset_name=preset_choices)
@discord.app_commands.guild_only
async def preset(
    interaction: discord.Interaction,
    action: Literal['list', 'save', 'load', 'delete'],
    preset_name: str | None = None
):
    member = cast(discord.Member, interaction.user)

    if preset_name is None and action != 'list':
        await reply_interaction(interaction, discord.Colour.red(), 'preset_cmd_no_preset_arg')
    elif action == 'list':
        presets = await get_presets(member)
        if len(presets) == 0:
            await reply_interaction(interaction, discord.Colour.dark_purple(), 'preset_cmd_no_presets')
            return
        presets = '- ' + '\n- '.join(preset.name for preset in presets)
        await reply_interaction(interaction, discord.Colour.dark_purple(), 'preset_cmd_list', presets=presets)
    elif action == 'load':
        preset = await get_preset(member, cast(str, preset_name))
        if preset is None:
            await reply_interaction(interaction, discord.Colour.red(), 'preset_cmd_does_not_exist')
        else:
            await update_voice(member, voice=preset.voice, pitch=preset.pitch, speed=preset.speed)
            await reply_interaction(interaction, discord.Colour.dark_purple(), 'preset_cmd_loaded')
    elif action == 'delete':
        if await delete_preset(member, cast(str, preset_name)):
            await reply_interaction(interaction, discord.Colour.dark_purple(), 'preset_cmd_deleted')
        else:
            await reply_interaction(interaction, discord.Colour.red(), 'preset_cmd_does_not_exist')
        return
    elif action == 'save':
        voice = await get_voice(member)
        if await save_preset(member, cast(str, preset_name), voice.voice, voice.pitch, voice.speed):
            await reply_interaction(interaction, discord.Colour.dark_purple(), 'preset_cmd_saved')
        else:
            await reply_interaction(interaction, discord.Colour.red(), 'preset_cmd_already_exists')


@tree.command(name='invite', description=translate('invite_cmd_description'))
@discord.app_commands.guild_only
async def invite(interaction: discord.Interaction):
    await reply_link_embed(interaction, client)


@tree.command(
    name='sync', description=translate('sync_cmd_description'), guild=discord.Object(id=args.dev_guild))
@discord.app_commands.guild_only
@discord.app_commands.checks.has_permissions(administrator=True)
async def sync(interaction: discord.Interaction):
    await tree.sync()
    await tree.sync(guild=interaction.guild)
    await reply_interaction(interaction, discord.Colour.dark_purple(), 'sync_cmd_sync_done')


@tree.command(name='config', description=translate('config_cmd_description'))
@discord.app_commands.describe(action=translate('config_cmd_action_argument_description'))
@discord.app_commands.guild_only
@discord.app_commands.checks.has_permissions(administrator=True)
async def config(interaction: discord.Interaction, action: Literal['get', 'set', 'cancel', 'default']):
    await config_command(interaction, action)


@tree.command(name='locales', description=translate('locales_cmd_description'))
@discord.app_commands.guild_only
@discord.app_commands.checks.has_permissions(administrator=True)
async def locales(interaction: discord.Interaction):
    locales = '- ' + '\n- '.join(supported_locales)
    await reply_interaction(interaction, discord.Colour.dark_purple(), 'locales_cmd_list', locales=locales)


@client.event
async def on_message(message: discord.Message):
    if message.guild is None or not isinstance(message.author, discord.Member):
        return
    if client.user in message.mentions and await on_config_mention(message):
        return
    if message.guild in readers:
        await readers[message.guild].on_message(message)


@client.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild is not None and isinstance(before.author, discord.Member) and before.guild in readers:
        await readers[before.guild].speaker.on_message_edit(after)


@client.event
async def on_message_delete(message: discord.Message):
    if message.guild is not None and isinstance(message.author, discord.Member) and message.guild in readers:
        await readers[message.guild].speaker.on_message_delete(message)


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel == after.channel:
        return

    if member == client.user:
        # the bot was disconnected, reset everything
        if after.channel is None:
            readers[member.guild] = GuildReader(member.guild)
        # the bot was moved to different channel, move it back
        if before.channel is not None:
            await readers[member.guild].go_to_master()
        return

    elif after.channel is None:
        await readers[member.guild].on_voice_disconnect(member)
    elif before.channel is None:
        await readers[member.guild].on_voice_connect(member)
    else:
        await readers[member.guild].on_voice_move(member)


@client.event
async def on_ready():
    await create_tables()
    await init_guild()

    for guild in client.guilds:
        reader = await init_guild(guild)
        assert reader is not None
        readers[guild] = reader

    logger.info(f'\u001b[32mLogged in as {client.user}')


def signal_handler(_: int, __: Any):
    client.loop.create_task(client.close())
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    client.run(os.environ['DISCORD_TOKEN'])
