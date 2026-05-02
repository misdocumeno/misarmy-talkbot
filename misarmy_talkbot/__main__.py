import os
import sys
import signal
import discord
from typing import Literal, Any, cast
from pydantic_core import ValidationError
from dotenv import load_dotenv
from .logger import logger
from .voice_log import format_discord_voice_state, format_guild_voice_snapshot
from .reader_debug import log_reader_forensics, readers_keys_identity_raw, readers_registry_table
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


if os.getenv('ENABLE_DEBUGPY') == '1':
    import debugpy

    debugpy.listen(('0.0.0.0', int(os.getenv('DEBUGPY_PORT', '5678'))))
    logger.info(f'Started debugpy on port {os.getenv("DEBUGPY_PORT", "5678")}')


intents = discord.Intents.default()
intents.members = True
intents.message_content = True
bot = discord.Bot(command_prefix='!', intents=intents)

readers: dict[discord.Guild, GuildReader] = {}


def _guild_reader(guild: discord.Guild) -> GuildReader | None:
    """Resolve reader with logging when the guild mapping is missing (e.g. during reconnect races)."""
    r = readers.get(guild)
    if r is None:
        logger.error(f'readers lookup miss guild_id={guild.id} guild_keys={[g.id for g in readers]}')
    return r


def _probe_readers_guild_key_alias(guild: discord.Guild, scope: str) -> None:
    """
    Detect if message.guild / ctx.guild is a different Python object than the dict key
    (same guild id, dict membership fails) or if two readers could appear for one guild id.
    """
    r_direct = readers.get(guild)
    r_scan = None
    k_scan = None
    for k, v in readers.items():
        if k.id == guild.id:
            r_scan = v
            k_scan = k
            break
    if r_direct is None and r_scan is not None:
        logger.critical(
            f'READER_KEY_MISS_GUILD_OBJ scope={scope} guild_id={guild.id} probe_guild_obj_id={id(guild)} '
            f'scan_key_obj_id={id(k_scan)} dict_key_eq_probe_guild={k_scan == guild} '
            f'reader_id={id(r_scan)} | {readers_registry_table(readers, scope)}'
        )
    elif r_direct is not None and r_scan is not None:
        if id(r_direct) != id(r_scan):
            logger.critical(
                f'READER_ID_MISMATCH_SAME_GUILD_ID scope={scope} guild_id={guild.id} '
                f'reader_via_get={id(r_direct)} reader_via_scan={id(r_scan)} | '
                f'{readers_registry_table(readers, scope)}'
            )
        if k_scan is not None and id(k_scan) != id(guild):
            logger.warning(
                f'READER_GUILD_KEY_OBJ_NEQ scope={scope} guild_id={guild.id} '
                f'probe_guild_obj_id={id(guild)} dict_key_obj_id={id(k_scan)} eq={guild == k_scan}'
            )
    logger.debug(f'PROBE_TAIL scope={scope} {readers_keys_identity_raw(readers)}')


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
    if ctx.guild is None:
        logger.warning('slash /follow skipped: guild is None (unexpected for guild_only)')
        return
    if ctx.guild not in readers:
        logger.error(
            f'slash /follow skipped: guild_id={ctx.guild.id} missing from readers keys={[g.id for g in readers]}'
        )
        _probe_readers_guild_key_alias(ctx.guild, 'slash_follow_miss')
        return
    if not isinstance(ctx.user, discord.Member):
        logger.warning(f'slash /follow skipped: user is not Member type={type(ctx.user)!r}')
        return
    _probe_readers_guild_key_alias(ctx.guild, 'slash_follow_pre')
    logger.info(readers_registry_table(readers, 'slash_follow_pre_table'))
    await ctx.defer(ephemeral=True)
    color, msgid = await readers[ctx.guild].follow(ctx.user)
    logger.info(
        f'slash /follow result guild_id={ctx.guild.id} user_id={ctx.user.id} outcome_msgid={msgid} '
        f'ctx_user_obj_id={id(ctx.user)} ctx_guild_obj_id={id(ctx.guild)} '
        f'reader_row={readers[ctx.guild].debug_registry_row()}'
    )
    logger.info(readers_registry_table(readers, 'slash_follow_post_table'))
    await reply_interaction(ctx, color, msgid)


@bot.command(name='unfollow', description=translate('unfollow_cmd_description'))
@discord.commands.guild_only()
async def unfollow(ctx: discord.ApplicationContext):
    if ctx.guild is None:
        logger.warning('slash /unfollow skipped: guild is None')
        return
    if ctx.guild not in readers:
        logger.error(
            f'slash /unfollow skipped: guild_id={ctx.guild.id} missing from readers keys={[g.id for g in readers]}'
        )
        _probe_readers_guild_key_alias(ctx.guild, 'slash_unfollow_miss')
        return
    if not isinstance(ctx.user, discord.Member):
        logger.warning(f'slash /unfollow skipped: user is not Member type={type(ctx.user)!r}')
        return
    _probe_readers_guild_key_alias(ctx.guild, 'slash_unfollow_pre')
    logger.info(readers_registry_table(readers, 'slash_unfollow_pre_table'))
    await ctx.defer(ephemeral=True)
    color, msgid = await readers[ctx.guild].unfollow(ctx.user)
    logger.info(
        f'slash /unfollow result guild_id={ctx.guild.id} user_id={ctx.user.id} '
        f'ctx_user_obj_id={id(ctx.user)} ctx_guild_obj_id={id(ctx.guild)} outcome_msgid={msgid} '
        f'reader_row={readers[ctx.guild].debug_registry_row()}'
    )
    logger.info(readers_registry_table(readers, 'slash_unfollow_post_table'))
    await reply_interaction(ctx, color, msgid)


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


@bot.command(name='debug', description=translate('debug_cmd_description'), guild=discord.Object(id=args.dev_guild))
@discord.commands.guild_only()
@discord.default_permissions(administrator=True)
async def debug(ctx: discord.ApplicationContext):
    await ctx.defer(ephemeral=True)
    try:
        debugpy.listen(('0.0.0.0', int(os.getenv('DEBUGPY_PORT', '5678'))))
        await reply_interaction(ctx, discord.Colour.dark_purple(), 'debug_cmd_debugger_started')
    except RuntimeError:
        await reply_interaction(ctx, discord.Colour.red(), 'debug_cmd_debugger_already_started')


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
    if message.guild not in readers:
        logger.warning(
            f'on_message no reader for guild_id={message.guild.id} message_id={message.id} skipped'
        )
        _probe_readers_guild_key_alias(message.guild, 'on_message_no_reader_key')
        return
    _probe_readers_guild_key_alias(message.guild, 'on_message_route')
    logger.debug(
        f'on_message route guild_id={message.guild.id} guild_obj_id={id(message.guild)} message_id={message.id} '
        f'author_id={message.author.id} reader_id={id(readers[message.guild])} '
        f'reader_row={readers[message.guild].debug_registry_row()}'
    )
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

    bot_vc = member.guild.voice_client
    before_s = format_discord_voice_state(before, 'before')
    after_s = format_discord_voice_state(after, 'after')
    guild_snap = format_guild_voice_snapshot(member.guild, 'during_event')

    logger.debug(
        f'on_voice_state_update member_id={member.id} bot={member.bot} bot_user_id={(bot.user.id if bot.user else None)} '
        f'guild_id={member.guild.id} {before_s} | {after_s} '
        f'guild_voice_protocol={bot_vc.__class__.__name__ if bot_vc else None}'
    )
    logger.debug(f'on_voice_state_update GUILD_SNAP {guild_snap}')

    if member == bot.user:
        # the bot was disconnected, reset everything
        if after.channel is None:
            await log_reader_forensics(
                logger, scope='PRE_BOT_VC_DROP_READER_REPLACE', readers=readers, guild_id=member.guild.id
            )
            logger.warning(f'PRE_REPLACE_SNAP_BOT_DISCONNECT {format_guild_voice_snapshot(member.guild, "snap")}')
            old_r = readers.get(member.guild)
            old_rid = id(old_r) if old_r else None
            old_sid = id(old_r.speaker) if old_r else None
            logger.warning(
                f'Bot voice DISCONNECT guild_id={member.guild.id} reader_will_replace=True '
                f'old_reader_id={old_rid} old_speaker_id={old_sid} '
                f'bot_before={before_s} bot_after={after_s} '
                f'| SEE TODO stale GuildSpeaker._speak keeps running on OLD speaker'
            )
            # TODO: we should stop the infinite loop in GuildSpeaker._speak first
            readers[member.guild] = GuildReader(member.guild)
            nr = readers[member.guild]
            logger.info(
                f'GuildReader replaced guild_id={member.guild.id} new_reader_id={id(nr)} '
                f'new_speaker_id={id(nr.speaker)} POST_REPLACE_SNAP {format_guild_voice_snapshot(member.guild, "snap")}'
            )
            await log_reader_forensics(
                logger, scope='POST_BOT_VC_DROP_READER_REPLACE', readers=readers, guild_id=member.guild.id
            )
        # the bot was moved to different channel, move it back (also runs after voice drop if before.channel set)
        if before.channel is not None:
            logger.info(
                f'Bot voice state left previous channel guild_id={member.guild.id} '
                f'before_ch={before.channel.id} after_ch={(after.channel.id if after.channel else None)} '
                f'calling go_to_master | {format_guild_voice_snapshot(member.guild, "snap")}'
            )
            await readers[member.guild].go_to_master()
        elif after.channel is not None:
            logger.info(
                f'Bot voice CONNECT (join without prior channel?) guild_id={member.guild.id} '
                f'channel_id={after.channel.id} | {format_guild_voice_snapshot(member.guild, "snap")}'
            )
        return

    gr = _guild_reader(member.guild)
    if gr is None:
        return

    if after.channel is None:
        await readers[member.guild].on_voice_disconnect(member)
    elif before.channel is None:
        await readers[member.guild].on_voice_connect(member)
    else:
        await readers[member.guild].on_voice_move(member)


@bot.event
async def on_connect():
    shards = getattr(bot, 'shard_count', None)
    rl = getattr(bot, 'is_ws_ratelimited', lambda: False)()
    logger.info(f'socket on_connect shards={shards} gw_ratelimited={rl}')


@bot.event
async def on_disconnect():
    rl = getattr(bot, 'is_ws_ratelimited', lambda: False)()
    logger.warning(
        f'socket on_disconnect (main WS closed); reconnect may follow gw_ratelimited={rl} '
        f'last_latency_sec={bot.latency:.6f}'
    )


@bot.event
async def on_resume():
    rl = getattr(bot, 'is_ws_ratelimited', lambda: False)()
    vc_guilds = sum(1 for g in bot.guilds if g.voice_client is not None)
    logger.info(
        f'session resumed (RESUME) gw_latency_sec={bot.latency:.6f} gw_ratelimited={rl} '
        f'guilds_with_voice_protocol={vc_guilds}/{len(bot.guilds)}'
    )
    logger.info(readers_registry_table(readers, 'on_resume'))


@bot.event
async def on_application_command_error(ctx: discord.ApplicationContext, error: discord.DiscordException):
    exc = getattr(error, 'original', error)
    cid = getattr(ctx, 'interaction', None)
    iid = cid.id if cid else None
    cmd = getattr(ctx.command, 'name', None)
    logger.error(
        f'application_command_error cmd={cmd!r} type={error.__class__.__name__} '
        f'guild_id={(ctx.guild.id if ctx.guild else None)} '
        f'channel_id={(ctx.channel.id if getattr(ctx.channel, "id", None) else None)} '
        f'user_id={(ctx.user.id if ctx.user else None)} interaction_id={iid}',
        exc_info=exc if isinstance(exc, BaseException) else error,
    )


@bot.event
async def on_ready():
    await create_tables()
    await init_guild()

    for guild in bot.guilds:
        reader = await init_guild(guild)
        assert reader is not None
        readers[guild] = reader

    logger.info(
        f'Logged in as {bot.user} (id={bot.user.id if bot.user else None}) guilds={len(bot.guilds)} '
        f'ws_latency_sec={bot.latency:.5f}'
    )
    for g in bot.guilds:
        r = readers.get(g)
        logger.debug(
            f'on_ready reader guild_id={g.id} reader_id={(id(r) if r else None)} '
            f'in_readers={(g in readers)}'
        )
    logger.info(readers_registry_table(readers, 'on_ready'))


def signal_handler(_: int, __: Any):
    bot.loop.create_task(bot.close())
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    bot.run(os.environ['DISCORD_TOKEN'])
