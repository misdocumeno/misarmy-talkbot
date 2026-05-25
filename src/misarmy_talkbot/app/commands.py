from __future__ import annotations

from typing import Literal, cast

import discord

from misarmy_talkbot.app.follow_ops import (
    follow_user,
    ignore_toggle,
    unfollow_user,
)
from misarmy_talkbot.args import args
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.config.command import config_command
from misarmy_talkbot.infra.database.preset import (
    delete_preset,
    get_preset,
    get_presets,
    save_preset,
)
from misarmy_talkbot.infra.database.voice import get_voice, update_voice
from misarmy_talkbot.infra.locale import supported_locales, translate
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.utils import (
    preset_choices,
    reply_interaction,
    reply_link_embed,
    voice_choices,
    voices_list,
)


def register_commands(bot: discord.Bot) -> None:
    """Register slash commands on ``bot``.

    Handlers stay thin and delegate to ``follow_ops``, database helpers, and
    ``GuildSessionRegistry`` so command text and permission decorators do not absorb
    domain rules that also apply to gateway events.
    """

    @bot.command(
        name='follow', description=translate('follow_cmd_description')
    )
    @discord.commands.guild_only()
    async def follow(ctx: discord.ApplicationContext) -> None:
        if ctx.guild is not None and isinstance(ctx.user, discord.Member):
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await follow_user(bot, ctx.guild, ctx.user)
            )

    @bot.command(
        name='unfollow', description=translate('unfollow_cmd_description')
    )
    @discord.commands.guild_only()
    async def unfollow(ctx: discord.ApplicationContext) -> None:
        if ctx.guild is not None and isinstance(ctx.user, discord.Member):
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await unfollow_user(bot, ctx.guild, ctx.user)
            )

    @bot.command(
        name='ignore', description=translate('ignore_cmd_description')
    )
    @discord.commands.guild_only()
    async def ignore(ctx: discord.ApplicationContext) -> None:
        if (
            ctx.guild is not None
            and isinstance(ctx.user, discord.Member)
            and isinstance(ctx.channel, discord.abc.Messageable)
        ):
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *ignore_toggle(ctx.guild, ctx.user, ctx.channel)
            )

    @bot.command(name='stop', description=translate('stop_cmd_description'))
    @discord.commands.guild_only()
    async def stop(ctx: discord.ApplicationContext) -> None:
        if ctx.guild is not None and isinstance(ctx.user, discord.Member):
            guild_session = GuildSessionRegistry.instance().get(ctx.guild.id)
            if guild_session is None:
                return
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await guild_session.engine.stop(ctx.user)
            )

    @bot.command(
        name='stopall', description=translate('stop_all_cmd_description')
    )
    @discord.default_permissions(mute_members=True)
    @discord.commands.guild_only()
    async def stopall(ctx: discord.ApplicationContext) -> None:
        if ctx.guild is not None:
            guild_session = GuildSessionRegistry.instance().get(ctx.guild.id)
            if guild_session is None:
                return
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await guild_session.engine.stop_all()
            )

    @bot.command(name='skip', description=translate('skip_cmd_description'))
    @discord.commands.guild_only()
    async def skip(ctx: discord.ApplicationContext) -> None:
        if ctx.guild is not None and isinstance(ctx.user, discord.Member):
            guild_session = GuildSessionRegistry.instance().get(ctx.guild.id)
            if guild_session is None:
                return
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await guild_session.engine.skip(ctx.user)
            )

    @bot.command(name='mute', description=translate('mute_cmd_description'))
    @discord.option(
        'target',
        description=translate('mute_cmd_target_argument_description'),
        type=discord.Member,
    )
    @discord.default_permissions(mute_members=True)
    @discord.commands.guild_only()
    async def mute(
        ctx: discord.ApplicationContext, target: discord.Member
    ) -> None:
        if ctx.guild is not None:
            guild_session = GuildSessionRegistry.instance().get(ctx.guild.id)
            if guild_session is None:
                return
            await ctx.defer(ephemeral=True)
            await reply_interaction(
                ctx, *await guild_session.engine.stop(target)
            )

    @bot.command(name='voice', description=translate('voice_cmd_description'))
    @discord.option(
        'new_voice',
        description=translate('voice_cmd_new_voice_argument_description'),
        type=str,
        required=False,
        autocomplete=discord.utils.basic_autocomplete(voice_choices),
    )
    @discord.commands.guild_only()
    async def voice(
        ctx: discord.ApplicationContext, new_voice: str | None = None
    ) -> None:
        await ctx.defer(ephemeral=True)
        color = discord.Colour.dark_purple()
        voice = await get_voice(cast('discord.Member', ctx.user))

        if new_voice is None:
            await reply_interaction(
                ctx, color, 'voice_cmd_current_voice', voice=voice.voice
            )
        elif new_voice not in await voices_list():
            await reply_interaction(
                ctx,
                discord.Colour.red(),
                'voice_cmd_invalid_voice',
                voice=new_voice,
            )
        elif await update_voice(
            cast('discord.Member', ctx.user), voice=new_voice
        ):
            await reply_interaction(
                ctx,
                color,
                'voice_cmd_new_voice',
                (
                    'voice_cmd_new_voice_footer'
                    if voice.speed != 1.0 or voice.pitch != 1.0
                    else ''
                ),
                voice=new_voice,
            )
        else:
            await reply_interaction(ctx, color, 'voice_cmd_same_voice')

    @bot.command(name='pitch', description=translate('pitch_cmd_description'))
    @discord.option(
        'new_pitch',
        description=translate('pitch_cmd_pitch_argument_description'),
        type=float,
        required=False,
    )
    @discord.commands.guild_only()
    async def pitch(
        ctx: discord.ApplicationContext, new_pitch: float | None = None
    ) -> None:
        await ctx.defer(ephemeral=True)
        color = discord.Colour.dark_purple()

        if new_pitch is None:
            voice = await get_voice(cast('discord.Member', ctx.user))
            await reply_interaction(
                ctx, color, 'pitch_cmd_current_pitch', pitch=voice.pitch
            )
        elif await update_voice(
            cast('discord.Member', ctx.user), pitch=new_pitch
        ):
            await reply_interaction(
                ctx, color, 'pitch_cmd_new_pitch', pitch=new_pitch
            )
        else:
            await reply_interaction(ctx, color, 'pitch_cmd_same_pitch')

    @bot.command(name='speed', description=translate('speed_cmd_description'))
    @discord.option(
        'new_speed',
        description=translate('speed_cmd_speed_argument_description'),
        type=float,
        required=False,
    )
    @discord.commands.guild_only()
    async def speed(
        ctx: discord.ApplicationContext, new_speed: float | None = None
    ) -> None:
        await ctx.defer(ephemeral=True)
        color = discord.Colour.dark_purple()

        if new_speed is None:
            voice = await get_voice(cast('discord.Member', ctx.user))
            await reply_interaction(
                ctx, color, 'speed_cmd_current_speed', speed=voice.speed
            )
        elif await update_voice(
            cast('discord.Member', ctx.user), speed=new_speed
        ):
            await reply_interaction(
                ctx, color, 'speed_cmd_new_speed', speed=new_speed
            )
        else:
            await reply_interaction(ctx, color, 'speed_cmd_same_speed')

    @bot.command(
        name='preset', description=translate('preset_cmd_description')
    )
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
        ctx: discord.ApplicationContext,
        action: Literal['list', 'save', 'load', 'delete'],
        preset_name: str | None = None,
    ) -> None:
        await ctx.defer(ephemeral=True)
        member = cast('discord.Member', ctx.user)

        if preset_name is None and action != 'list':
            await reply_interaction(
                ctx, discord.Colour.red(), 'preset_cmd_no_preset_arg'
            )
        elif action == 'list':
            presets = await get_presets(member)
            if len(presets) == 0:
                await reply_interaction(
                    ctx, discord.Colour.dark_purple(), 'preset_cmd_no_presets'
                )
                return
            preset_lines = '- ' + '\n- '.join(
                preset.name for preset in presets
            )
            await reply_interaction(
                ctx,
                discord.Colour.dark_purple(),
                'preset_cmd_list',
                presets=preset_lines,
            )
        elif action == 'load':
            preset = await get_preset(member, cast('str', preset_name))
            if preset is None:
                await reply_interaction(
                    ctx, discord.Colour.red(), 'preset_cmd_does_not_exist'
                )
            else:
                await update_voice(
                    member,
                    voice=preset.voice,
                    pitch=preset.pitch,
                    speed=preset.speed,
                )
                await reply_interaction(
                    ctx, discord.Colour.dark_purple(), 'preset_cmd_loaded'
                )
        elif action == 'delete':
            if await delete_preset(member, cast('str', preset_name)):
                await reply_interaction(
                    ctx, discord.Colour.dark_purple(), 'preset_cmd_deleted'
                )
            else:
                await reply_interaction(
                    ctx, discord.Colour.red(), 'preset_cmd_does_not_exist'
                )
        elif action == 'save':
            voice = await get_voice(member)
            if await save_preset(
                member,
                cast('str', preset_name),
                voice.voice,
                voice.pitch,
                voice.speed,
            ):
                await reply_interaction(
                    ctx, discord.Colour.dark_purple(), 'preset_cmd_saved'
                )
            else:
                await reply_interaction(
                    ctx, discord.Colour.red(), 'preset_cmd_already_exists'
                )

    @bot.command(
        name='invite', description=translate('invite_cmd_description')
    )
    @discord.commands.guild_only()
    async def invite(ctx: discord.ApplicationContext) -> None:
        await reply_link_embed(ctx, bot)

    @bot.command(
        name='sync',
        description=translate('sync_cmd_description'),
        guild=discord.Object(id=args.dev_guild),
    )
    @discord.commands.guild_only()
    @discord.default_permissions(administrator=True)
    async def sync(ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        await ctx.bot.sync_commands()

        if ctx.guild is not None:
            await ctx.bot.sync_commands(guild_ids=[ctx.guild.id])

        await reply_interaction(
            ctx, discord.Colour.dark_purple(), 'sync_cmd_sync_done'
        )

    @bot.command(
        name='debug',
        description=translate('debug_cmd_description'),
        guild=discord.Object(id=args.dev_guild),
    )
    @discord.commands.guild_only()
    @discord.default_permissions(administrator=True)
    async def debug(ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        from misarmy_talkbot.observability.debugpy_boot import (
            start_debugpy_if_enabled,
        )

        start_debugpy_if_enabled()
        await reply_interaction(
            ctx, discord.Colour.dark_purple(), 'debug_cmd_debugger_started'
        )

    @bot.command(
        name='config', description=translate('config_cmd_description')
    )
    @discord.option(
        'action',
        description=translate('config_cmd_action_argument_description'),
        type=str,
        choices=['get', 'set', 'cancel', 'default'],
    )
    @discord.commands.guild_only()
    @discord.default_permissions(administrator=True)
    async def config(
        ctx: discord.ApplicationContext,
        action: Literal['get', 'set', 'cancel', 'default'],
    ) -> None:
        await config_command(ctx, action)

    @bot.command(
        name='locales', description=translate('locales_cmd_description')
    )
    @discord.commands.guild_only()
    @discord.default_permissions(administrator=True)
    async def locales(ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        locale_lines = '- ' + '\n- '.join(supported_locales)
        await reply_interaction(
            ctx,
            discord.Colour.dark_purple(),
            'locales_cmd_list',
            locales=locale_lines,
        )

    @bot.command(
        name='status',
        description='Show talkbot metrics for this guild (admin).',
    )
    @discord.commands.guild_only()
    @discord.default_permissions(administrator=True)
    async def status_cmd(ctx: discord.ApplicationContext) -> None:
        await ctx.defer(ephemeral=True)
        if ctx.guild is None:
            return
        metrics_registry = MetricsRegistry.instance()
        rows = metrics_registry.snapshot_guild_embed_fields(ctx.guild.id)
        embed = discord.Embed(
            title='Talkbot status', color=discord.Colour.dark_purple()
        )
        for name, val in rows:
            embed.add_field(name=name, value=val, inline=False)
        if not rows:
            embed.description = 'No metrics recorded yet for this guild.'
        await ctx.respond(embed=embed, ephemeral=True)
