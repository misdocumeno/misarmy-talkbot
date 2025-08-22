import os
import sys
import signal
import discord
from typing import Literal, Any, cast
from pydantic_core import ValidationError
from dotenv import load_dotenv
from .logger import logger
from .tts.reader import GuildReader
from .locale.translations import translate, supported_locales
from .utils import reply_interaction, reply_link_embed, voice_choices, preset_choices, voices_list
from .config.config import update_config, get_config_json, set_default_config, default_config
from .config.command import config_command, on_config_mention
from .database.database import create_tables
from .database.voice import get_voice, update_voice
from .database.preset import get_preset, get_presets, save_preset, delete_preset
from .args import args


load_dotenv(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Bot(command_prefix='!', intents=intents)

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


@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f'Joined {guild.name} ({guild.id})')
    reader = await init_guild(guild)
    assert reader is not None
    readers[guild] = reader


@bot.event
async def on_guild_remove(guild: discord.Guild):
    logger.info(f'Left {guild.name} ({guild.id})')
    del readers[guild]


@bot.command(name='follow', description=translate('follow_cmd_description'))
@discord.commands.guild_only()
async def follow(ctx: discord.ApplicationContext):
    if ctx.guild is not None and ctx.guild in readers and isinstance(ctx.user, discord.Member):
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].follow(ctx.user))


@bot.command(name='unfollow', description=translate('unfollow_cmd_description'))
@discord.commands.guild_only()
async def unfollow(ctx: discord.ApplicationContext):
    if ctx.guild is not None and ctx.guild in readers and isinstance(ctx.user, discord.Member):
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].unfollow(ctx.user))


@bot.command(name='ignore', description=translate('ignore_cmd_description'))
@discord.commands.guild_only()
async def ignore(ctx: discord.ApplicationContext):
    if (
        ctx.guild is not None
        and ctx.guild in readers
        and isinstance(ctx.user, discord.Member)
        and isinstance(ctx.channel, discord.abc.Messageable)
    ):
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *readers[ctx.guild].ignore(ctx.user, ctx.channel))


@bot.command(name='stop', description=translate('stop_cmd_description'))
@discord.commands.guild_only()
async def stop(ctx: discord.ApplicationContext):
    if ctx.guild is not None and ctx.guild in readers and isinstance(ctx.user, discord.Member):
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].speaker.stop(ctx.user))


@bot.command(name='stopall', description=translate('stop_all_cmd_description'))
@discord.default_permissions(mute_members=True)
@discord.commands.guild_only()
async def stopall(ctx: discord.ApplicationContext):
    if ctx.guild is not None and ctx.guild in readers:
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].speaker.stop_all())


@bot.command(name='skip', description=translate('skip_cmd_description'))
@discord.commands.guild_only()
async def skip(ctx: discord.ApplicationContext):
    if ctx.guild is not None and ctx.guild in readers and isinstance(ctx.user, discord.Member):
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].speaker.skip(ctx.user))


@bot.command(name='mute', description=translate('mute_cmd_description'))
@discord.option('target', description=translate('mute_cmd_target_argument_description'), type=discord.Member)
@discord.default_permissions(mute_members=True)
@discord.commands.guild_only()
async def mute(ctx: discord.ApplicationContext, target: discord.Member):
    if ctx.guild is not None and ctx.guild in readers:
        await ctx.defer(ephemeral=True)
        await reply_interaction(ctx, *await readers[ctx.guild].speaker.stop(target))


@bot.command(name='voice', description=translate('voice_cmd_description'))
@discord.option(
    'new_voice',
    description=translate('voice_cmd_new_voice_argument_description'),
    type=str,
    required=False,
    autocomplete=discord.utils.basic_autocomplete(voice_choices),
)
@discord.commands.guild_only()
async def voice(ctx: discord.ApplicationContext, new_voice: str | None = None):
    await ctx.defer(ephemeral=True)
    color = discord.Colour.dark_purple()
    voice = await get_voice(cast(discord.Member, ctx.user))

    if new_voice is None:
        await reply_interaction(ctx, color, 'voice_cmd_current_voice', voice=voice.voice)
    elif new_voice not in await voices_list():
        await reply_interaction(ctx, discord.Colour.red(), 'voice_cmd_invalid_voice', voice=new_voice)
    elif await update_voice(cast(discord.Member, ctx.user), voice=new_voice):
        await reply_interaction(
            ctx,
            color,
            'voice_cmd_new_voice',
            'voice_cmd_new_voice_footer' if voice.speed != 1.0 or voice.pitch != 1.0 else '',
            voice=new_voice,
        )
    else:
        await reply_interaction(ctx, color, 'voice_cmd_same_voice')


@bot.command(name='pitch', description=translate('pitch_cmd_description'))
@discord.option('new_pitch', description=translate('pitch_cmd_pitch_argument_description'), type=float, required=False)
@discord.commands.guild_only()
async def pitch(ctx: discord.ApplicationContext, new_pitch: float | None = None):
    await ctx.defer(ephemeral=True)
    color = discord.Colour.dark_purple()

    if new_pitch is None:
        voice = await get_voice(cast(discord.Member, ctx.user))
        await reply_interaction(ctx, color, 'pitch_cmd_current_pitch', pitch=voice.pitch)
    elif await update_voice(cast(discord.Member, ctx.user), pitch=new_pitch):
        await reply_interaction(ctx, color, 'pitch_cmd_new_pitch', pitch=new_pitch)
    else:
        await reply_interaction(ctx, color, 'pitch_cmd_same_pitch')


@bot.command(name='speed', description=translate('speed_cmd_description'))
@discord.option('new_speed', description=translate('speed_cmd_speed_argument_description'), type=float, required=False)
@discord.commands.guild_only()
async def speed(ctx: discord.ApplicationContext, new_speed: float | None = None):
    await ctx.defer(ephemeral=True)
    color = discord.Colour.dark_purple()

    if new_speed is None:
        voice = await get_voice(cast(discord.Member, ctx.user))
        await reply_interaction(ctx, color, 'speed_cmd_current_speed', speed=voice.speed)
    elif await update_voice(cast(discord.Member, ctx.user), speed=new_speed):
        await reply_interaction(ctx, color, 'speed_cmd_new_speed', speed=new_speed)
    else:
        await reply_interaction(ctx, color, 'speed_cmd_same_speed')


@bot.command(name='preset', description=translate('preset_cmd_description'))
@discord.option(
    'action',
    description=translate('preset_cmd_action_argument_description'),
    type=str,
    choices=['list', 'save', 'load', 'delete'],
)
@discord.option(
    'preset_name',
    description=translate('preset_cmd_preset_name_argument_description'),
    type=str,
    required=False,
    autocomplete=discord.utils.basic_autocomplete(preset_choices),
)
@discord.commands.guild_only()
async def preset(
    ctx: discord.ApplicationContext, action: Literal['list', 'save', 'load', 'delete'], preset_name: str | None = None
):
    await ctx.defer(ephemeral=True)
    member = cast(discord.Member, ctx.user)

    if preset_name is None and action != 'list':
        await reply_interaction(ctx, discord.Colour.red(), 'preset_cmd_no_preset_arg')
    elif action == 'list':
        presets = await get_presets(member)
        if len(presets) == 0:
            await reply_interaction(ctx, discord.Colour.dark_purple(), 'preset_cmd_no_presets')
            return
        presets = '- ' + '\n- '.join(preset.name for preset in presets)
        await reply_interaction(ctx, discord.Colour.dark_purple(), 'preset_cmd_list', presets=presets)
    elif action == 'load':
        preset = await get_preset(member, cast(str, preset_name))
        if preset is None:
            await reply_interaction(ctx, discord.Colour.red(), 'preset_cmd_does_not_exist')
        else:
            await update_voice(member, voice=preset.voice, pitch=preset.pitch, speed=preset.speed)
            await reply_interaction(ctx, discord.Colour.dark_purple(), 'preset_cmd_loaded')
    elif action == 'delete':
        if await delete_preset(member, cast(str, preset_name)):
            await reply_interaction(ctx, discord.Colour.dark_purple(), 'preset_cmd_deleted')
        else:
            await reply_interaction(ctx, discord.Colour.red(), 'preset_cmd_does_not_exist')
        return
    elif action == 'save':
        voice = await get_voice(member)
        if await save_preset(member, cast(str, preset_name), voice.voice, voice.pitch, voice.speed):
            await reply_interaction(ctx, discord.Colour.dark_purple(), 'preset_cmd_saved')
        else:
            await reply_interaction(ctx, discord.Colour.red(), 'preset_cmd_already_exists')


@bot.command(name='invite', description=translate('invite_cmd_description'))
@discord.commands.guild_only()
async def invite(ctx: discord.ApplicationContext):
    await reply_link_embed(ctx, bot)


@bot.command(name='sync', description=translate('sync_cmd_description'), guild=discord.Object(id=args.dev_guild))
@discord.commands.guild_only()
@discord.default_permissions(administrator=True)
async def sync(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    await ctx.bot.sync_commands()

    if ctx.guild is not None:
        await ctx.bot.sync_commands(guild_ids=[ctx.guild.id])

    await reply_interaction(ctx, discord.Colour.dark_purple(), 'sync_cmd_sync_done')


@bot.command(name='config', description=translate('config_cmd_description'))
@discord.option(
    'action',
    description=translate('config_cmd_action_argument_description'),
    type=str,
    choices=['get', 'set', 'cancel', 'default'],
)
@discord.commands.guild_only()
@discord.default_permissions(administrator=True)
async def config(ctx: discord.ApplicationContext, action: Literal['get', 'set', 'cancel', 'default']):
    await config_command(ctx, action)


@bot.command(name='locales', description=translate('locales_cmd_description'))
@discord.commands.guild_only()
@discord.default_permissions(administrator=True)
async def locales(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    locales = '- ' + '\n- '.join(supported_locales)
    await reply_interaction(ctx, discord.Colour.dark_purple(), 'locales_cmd_list', locales=locales)


@bot.event
async def on_message(message: discord.Message):
    if message.guild is None or not isinstance(message.author, discord.Member):
        return
    if bot.user in message.mentions and await on_config_mention(message):
        return
    if message.guild in readers:
        await readers[message.guild].on_message(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if before.guild is not None and isinstance(before.author, discord.Member) and before.guild in readers:
        await readers[before.guild].speaker.on_message_edit(after)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild is not None and isinstance(message.author, discord.Member) and message.guild in readers:
        await readers[message.guild].speaker.on_message_delete(message)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel == after.channel:
        return

    if member == bot.user:
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


@bot.event
async def on_ready():
    await create_tables()
    await init_guild()

    for guild in bot.guilds:
        reader = await init_guild(guild)
        assert reader is not None
        readers[guild] = reader

    logger.info(f'\u001b[32mLogged in as {bot.user}')


def signal_handler(_: int, __: Any):
    bot.loop.create_task(bot.close())
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    bot.run(os.environ['DISCORD_TOKEN'])
