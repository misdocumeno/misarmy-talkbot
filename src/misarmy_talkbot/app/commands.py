"""Slash-command registration for the Discord bot tree."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

import discord
from discord import app_commands

from misarmy_talkbot.app.follow_ops import (
    follow_user,
    ignore_toggle,
    unfollow_user,
)
from misarmy_talkbot.app.tts_usage_access import can_run_tts_usage
from misarmy_talkbot.args import args
from misarmy_talkbot.core.playback.tts.exceptions import ProsodyValidationError
from misarmy_talkbot.core.playback.tts.registry import TtsRegistry
from misarmy_talkbot.core.playback.tts.settings import (
    load_azure_speech_settings,
    load_google_cloud_tts_settings,
)
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.infra.config.command import config_command
from misarmy_talkbot.infra.database.preset import (
    delete_preset,
    get_preset,
    get_presets,
    save_preset,
)
from misarmy_talkbot.infra.database.tts_usage import TtsUsageStore
from misarmy_talkbot.infra.database.voice import get_voice, update_voice
from misarmy_talkbot.infra.locale import supported_locales, translate
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.utils import (
    preset_autocomplete,
    reply_interaction,
    reply_link_embed,
    voice_autocomplete,
    voices_list,
)

if TYPE_CHECKING:
    from discord.ext import commands


def register_commands(bot: commands.Bot) -> None:
    """Register slash commands on ``bot.tree``.

    Handlers stay thin and delegate to ``follow_ops``, database helpers, and
    ``GuildSessionRegistry`` so command text and permission decorators do not
    absorb domain rules that also apply to gateway events.
    """

    dev_guild = discord.Object(id=args.dev_guild)

    @bot.tree.command(
        name='follow', description=translate('follow_cmd_description')
    )
    @app_commands.guild_only()
    async def follow(interaction: discord.Interaction) -> None:
        if interaction.guild is not None and isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction,
                *await follow_user(bot, interaction.guild, interaction.user),
            )

    @bot.tree.command(
        name='unfollow', description=translate('unfollow_cmd_description')
    )
    @app_commands.guild_only()
    async def unfollow(interaction: discord.Interaction) -> None:
        if interaction.guild is not None and isinstance(
            interaction.user, discord.Member
        ):
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction,
                *await unfollow_user(bot, interaction.guild, interaction.user),
            )

    @bot.tree.command(
        name='ignore', description=translate('ignore_cmd_description')
    )
    @app_commands.guild_only()
    async def ignore(interaction: discord.Interaction) -> None:
        if (
            interaction.guild is not None
            and isinstance(interaction.user, discord.Member)
            and isinstance(interaction.channel, discord.abc.Messageable)
        ):
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction,
                *ignore_toggle(
                    interaction.guild, interaction.user, interaction.channel
                ),
            )

    @bot.tree.command(
        name='stop', description=translate('stop_cmd_description')
    )
    @app_commands.guild_only()
    async def stop(interaction: discord.Interaction) -> None:
        if interaction.guild is not None and isinstance(
            interaction.user, discord.Member
        ):
            guild_session = GuildSessionRegistry.instance().get(
                interaction.guild.id
            )
            if guild_session is None:
                return
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction, *await guild_session.engine.stop(interaction.user)
            )

    @bot.tree.command(
        name='stopall', description=translate('stop_all_cmd_description')
    )
    @app_commands.default_permissions(mute_members=True)
    @app_commands.guild_only()
    async def stopall(interaction: discord.Interaction) -> None:
        if interaction.guild is not None:
            guild_session = GuildSessionRegistry.instance().get(
                interaction.guild.id
            )
            if guild_session is None:
                return
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction, *await guild_session.engine.stop_all()
            )

    @bot.tree.command(
        name='skip', description=translate('skip_cmd_description')
    )
    @app_commands.guild_only()
    async def skip(interaction: discord.Interaction) -> None:
        if interaction.guild is not None and isinstance(
            interaction.user, discord.Member
        ):
            guild_session = GuildSessionRegistry.instance().get(
                interaction.guild.id
            )
            if guild_session is None:
                return
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction, *await guild_session.engine.skip(interaction.user)
            )

    @bot.tree.command(
        name='mute', description=translate('mute_cmd_description')
    )
    @app_commands.describe(
        target=translate('mute_cmd_target_argument_description')
    )
    @app_commands.default_permissions(mute_members=True)
    @app_commands.guild_only()
    async def mute(
        interaction: discord.Interaction, target: discord.Member
    ) -> None:
        if interaction.guild is not None:
            guild_session = GuildSessionRegistry.instance().get(
                interaction.guild.id
            )
            if guild_session is None:
                return
            await interaction.response.defer(ephemeral=True)
            await reply_interaction(
                interaction, *await guild_session.engine.stop(target)
            )

    @bot.tree.command(
        name='voice', description=translate('voice_cmd_description')
    )
    @app_commands.describe(
        new_voice=translate('voice_cmd_new_voice_argument_description')
    )
    @app_commands.guild_only()
    async def voice_cmd(
        interaction: discord.Interaction, new_voice: str | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        color = discord.Colour.dark_purple()
        member = cast('discord.Member', interaction.user)
        voice = await get_voice(member)

        if new_voice is None:
            await reply_interaction(
                interaction,
                color,
                'voice_cmd_current_voice',
                voice=voice.voice,
            )
        else:
            try:
                new_voice = TtsRegistry.instance().canonicalize_voice(
                    new_voice
                )
            except ValueError:
                pass
            if new_voice not in await voices_list():
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'voice_cmd_invalid_voice',
                    voice=new_voice,
                )
            elif await update_voice(member, voice=new_voice):
                await reply_interaction(
                    interaction,
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
                await reply_interaction(
                    interaction, color, 'voice_cmd_same_voice'
                )

    voice_cmd.autocomplete('new_voice')(voice_autocomplete)

    @bot.tree.command(
        name='pitch', description=translate('pitch_cmd_description')
    )
    @app_commands.describe(
        new_pitch=translate('pitch_cmd_pitch_argument_description')
    )
    @app_commands.guild_only()
    async def pitch(
        interaction: discord.Interaction, new_pitch: float | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        color = discord.Colour.dark_purple()
        member = cast('discord.Member', interaction.user)

        if new_pitch is None:
            voice = await get_voice(member)
            await reply_interaction(
                interaction,
                color,
                'pitch_cmd_current_pitch',
                pitch=voice.pitch,
            )
        else:
            try:
                changed = await update_voice(member, pitch=new_pitch)
            except ProsodyValidationError as exc:
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'pitch_cmd_invalid_pitch',
                    detail=str(exc),
                )
                return
            if changed:
                await reply_interaction(
                    interaction, color, 'pitch_cmd_new_pitch', pitch=new_pitch
                )
            else:
                await reply_interaction(
                    interaction, color, 'pitch_cmd_same_pitch'
                )

    @bot.tree.command(
        name='speed', description=translate('speed_cmd_description')
    )
    @app_commands.describe(
        new_speed=translate('speed_cmd_speed_argument_description')
    )
    @app_commands.guild_only()
    async def speed(
        interaction: discord.Interaction, new_speed: float | None = None
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        color = discord.Colour.dark_purple()
        member = cast('discord.Member', interaction.user)

        if new_speed is None:
            voice = await get_voice(member)
            await reply_interaction(
                interaction,
                color,
                'speed_cmd_current_speed',
                speed=voice.speed,
            )
        else:
            try:
                changed = await update_voice(member, speed=new_speed)
            except ProsodyValidationError as exc:
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'speed_cmd_invalid_speed',
                    detail=str(exc),
                )
                return
            if changed:
                await reply_interaction(
                    interaction, color, 'speed_cmd_new_speed', speed=new_speed
                )
            else:
                await reply_interaction(
                    interaction, color, 'speed_cmd_same_speed'
                )

    @bot.tree.command(
        name='preset', description=translate('preset_cmd_description')
    )
    @app_commands.describe(
        action=translate('preset_cmd_action_argument_description'),
        preset_name=translate('preset_cmd_preset_name_argument_description'),
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name='list', value='list'),
            app_commands.Choice(name='save', value='save'),
            app_commands.Choice(name='load', value='load'),
            app_commands.Choice(name='delete', value='delete'),
        ]
    )
    @app_commands.guild_only()
    async def preset(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        preset_name: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        member = cast('discord.Member', interaction.user)
        action_value: Literal['list', 'save', 'load', 'delete'] = cast(
            "Literal['list', 'save', 'load', 'delete']", action.value
        )

        if preset_name is None and action_value != 'list':
            await reply_interaction(
                interaction, discord.Colour.red(), 'preset_cmd_no_preset_arg'
            )
        elif action_value == 'list':
            presets = await get_presets(member)
            if len(presets) == 0:
                await reply_interaction(
                    interaction,
                    discord.Colour.dark_purple(),
                    'preset_cmd_no_presets',
                )
                return
            preset_lines = '- ' + '\n- '.join(
                preset.name for preset in presets
            )
            await reply_interaction(
                interaction,
                discord.Colour.dark_purple(),
                'preset_cmd_list',
                presets=preset_lines,
            )
        elif action_value == 'load':
            loaded = await get_preset(member, cast('str', preset_name))
            if loaded is None:
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'preset_cmd_does_not_exist',
                )
            else:
                try:
                    await update_voice(
                        member,
                        voice=loaded.voice,
                        pitch=loaded.pitch,
                        speed=loaded.speed,
                    )
                except ProsodyValidationError as exc:
                    await reply_interaction(
                        interaction,
                        discord.Colour.red(),
                        'preset_cmd_invalid_prosody',
                        detail=str(exc),
                    )
                    return
                await reply_interaction(
                    interaction,
                    discord.Colour.dark_purple(),
                    'preset_cmd_loaded',
                )

        elif action_value == 'delete':
            if await delete_preset(member, cast('str', preset_name)):
                await reply_interaction(
                    interaction,
                    discord.Colour.dark_purple(),
                    'preset_cmd_deleted',
                )
            else:
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'preset_cmd_does_not_exist',
                )
        elif action_value == 'save':
            voice = await get_voice(member)
            if await save_preset(
                member,
                cast('str', preset_name),
                voice.voice,
                voice.pitch,
                voice.speed,
            ):
                await reply_interaction(
                    interaction,
                    discord.Colour.dark_purple(),
                    'preset_cmd_saved',
                )
            else:
                await reply_interaction(
                    interaction,
                    discord.Colour.red(),
                    'preset_cmd_already_exists',
                )

    preset.autocomplete('preset_name')(preset_autocomplete)

    @bot.tree.command(
        name='invite', description=translate('invite_cmd_description')
    )
    @app_commands.guild_only()
    async def invite(interaction: discord.Interaction) -> None:
        await reply_link_embed(interaction, bot)

    @bot.tree.command(
        name='sync',
        description=translate('sync_cmd_description'),
        guild=dev_guild,
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def sync_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await bot.tree.sync()
        if interaction.guild is not None:
            await bot.tree.sync(guild=interaction.guild)
        await reply_interaction(
            interaction, discord.Colour.dark_purple(), 'sync_cmd_sync_done'
        )

    @bot.tree.command(
        name='debug',
        description=translate('debug_cmd_description'),
        guild=dev_guild,
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def debug(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        from misarmy_talkbot.observability.debugpy_boot import (
            start_debugpy_if_enabled,
        )

        start_debugpy_if_enabled()
        await reply_interaction(
            interaction,
            discord.Colour.dark_purple(),
            'debug_cmd_debugger_started',
        )

    @bot.tree.command(
        name='config', description=translate('config_cmd_description')
    )
    @app_commands.describe(
        action=translate('config_cmd_action_argument_description')
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name='get', value='get'),
            app_commands.Choice(name='set', value='set'),
            app_commands.Choice(name='cancel', value='cancel'),
            app_commands.Choice(name='default', value='default'),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def config(
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
    ) -> None:
        action_value: Literal['get', 'set', 'cancel', 'default'] = cast(
            "Literal['get', 'set', 'cancel', 'default']", action.value
        )
        await config_command(interaction, action_value)

    @bot.tree.command(
        name='locales', description=translate('locales_cmd_description')
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def locales(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        locale_lines = '- ' + '\n- '.join(supported_locales)
        await reply_interaction(
            interaction,
            discord.Colour.dark_purple(),
            'locales_cmd_list',
            locales=locale_lines,
        )

    @bot.tree.command(
        name='status',
        description='Show talkbot metrics for this guild (admin).',
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def status_cmd(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            return
        metrics_registry = MetricsRegistry.instance()
        rows = metrics_registry.snapshot_guild_embed_fields(
            interaction.guild.id
        )
        embed = discord.Embed(
            title='Talkbot status', color=discord.Colour.dark_purple()
        )
        for name, val in rows:
            embed.add_field(name=name, value=val, inline=False)
        if not rows:
            embed.description = 'No metrics recorded yet for this guild.'
        await interaction.followup.send(embed=embed, ephemeral=True)

    @bot.tree.command(
        name='tts-usage',
        description=translate('tts_usage_cmd_description'),
    )
    @app_commands.describe(
        days=translate('tts_usage_cmd_days_argument_description')
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    async def tts_usage_cmd(
        interaction: discord.Interaction, days: int = 30
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild is None:
            return
        if not can_run_tts_usage(
            user_id=interaction.user.id, guild_id=interaction.guild.id
        ):
            await interaction.followup.send(
                translate('tts_usage_cmd_denied', interaction.guild),
                ephemeral=True,
            )
            return
        clamped_days = max(1, min(days, 90))
        azure_settings = load_azure_speech_settings()
        google_settings = load_google_cloud_tts_settings()
        report = await TtsUsageStore.instance().build_report(
            days=clamped_days,
            guild_id=interaction.guild.id,
            azure_month_cap=azure_settings.monthly_cap,
            google_cloud_month_cap=google_settings.monthly_cap,
            google_bucket_caps={
                'standard_wavenet': google_settings.cap_standard_wavenet,
                'neural2': google_settings.cap_neural2,
                'chirp3': google_settings.cap_chirp3,
                'studio': (
                    google_settings.cap_studio
                    if google_settings.allow_studio
                    else 0
                ),
            },
            azure_quota_month_day=azure_settings.quota_month_day,
            azure_quota_tz=azure_settings.quota_tz,
            google_quota_month_day=google_settings.quota_month_day,
            google_quota_tz=google_settings.quota_tz,
        )
        embed = discord.Embed(
            title=translate('tts_usage_cmd_title', interaction.guild).format(
                days=report.days
            ),
            color=discord.Colour.dark_purple(),
        )
        embed.add_field(
            name=translate('tts_usage_cmd_totals', interaction.guild),
            value=(
                f'{report.total_chars:,} chars / {report.total_requests} ok '
                f'(window since {report.since.date().isoformat()} UTC)'
            ),
            inline=False,
        )
        if report.by_provider:
            provider_lines = '\n'.join(
                (
                    f'`{summary.provider}`: {summary.chars:,} chars, '
                    f'{summary.requests} req, ~${summary.est_usd:.2f}'
                )
                for summary in report.by_provider
            )
            embed.add_field(
                name=translate('tts_usage_cmd_by_provider', interaction.guild),
                value=provider_lines,
                inline=False,
            )
        azure_cap_label = (
            f'{report.azure_month_cap:,}'
            if report.azure_month_cap > 0
            else 'unlimited'
        )
        azure_pct = (
            f' ({100 * report.azure_month_chars / report.azure_month_cap:.0f}%)'
            if report.azure_month_cap > 0
            else ''
        )
        embed.add_field(
            name=translate('tts_usage_cmd_azure_month', interaction.guild),
            value=(
                f'{report.azure_month_chars:,} / {azure_cap_label}{azure_pct}\n'
                f'_F0 also caps ~20 synth requests / 60s (rate, not chars)_'
            ),
            inline=False,
        )
        if report.google_buckets:
            bucket_lines: list[str] = []
            for bucket in report.google_buckets:
                if bucket.bucket == 'studio' and bucket.cap <= 0:
                    bucket_lines.append(
                        f'`{bucket.label}`: not listed '
                        f'(local={bucket.chars:,})'
                    )
                    continue
                cap_label = (
                    f'{bucket.cap:,}' if bucket.cap > 0 else 'unlimited'
                )
                pct = (
                    f' ({100 * bucket.chars / bucket.cap:.0f}%)'
                    if bucket.cap > 0
                    else ''
                )
                bucket_lines.append(
                    f'`{bucket.label}`: {bucket.chars:,} / {cap_label}{pct}'
                )
            embed.add_field(
                name=translate(
                    'tts_usage_cmd_google_buckets', interaction.guild
                ),
                value='\n'.join(bucket_lines),
                inline=False,
            )
        embed.add_field(
            name=translate(
                'tts_usage_cmd_google_cloud_month', interaction.guild
            ),
            value=(
                f'All Cloud backends combined: '
                f'{report.google_cloud_month_chars:,} chars (UTC month)'
            ),
            inline=False,
        )
        if report.top_guilds:
            embed.add_field(
                name=translate('tts_usage_cmd_top_guilds', interaction.guild),
                value='\n'.join(
                    f'`{guild_id}`: {chars:,}'
                    for guild_id, chars in report.top_guilds
                ),
                inline=False,
            )
        if report.top_users:
            embed.add_field(
                name=translate('tts_usage_cmd_top_users', interaction.guild),
                value='\n'.join(
                    f'<@{user_id}>: {chars:,}'
                    for user_id, chars in report.top_users
                ),
                inline=False,
            )
        if report.guild_top_users:
            embed.add_field(
                name=translate(
                    'tts_usage_cmd_guild_top_users', interaction.guild
                ),
                value='\n'.join(
                    f'<@{user_id}>: {chars:,}'
                    for user_id, chars in report.guild_top_users
                ),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)
