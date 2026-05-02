"""
Discord voice / VC snapshots for forensic logging (tokens are never logged).
"""

from __future__ import annotations

import discord


def format_discord_voice_state(vs: discord.VoiceState | None, label: str = 'voice') -> str:
    """Log everything we get from GATEWAY_VOICE_STATE_UPDATES-derived VoiceState."""
    if vs is None:
        return f'{label}=<None>'
    ch = vs.channel
    rts = vs.requested_to_speak_at
    ch_oid = id(ch) if ch is not None else None
    return (
        f'{label}('
        f'channel_snowflake_id={(ch.id if ch else None)} channel_obj_id={ch_oid} '
        f'session_id={vs.session_id!r} '
        f'self_deaf={vs.self_deaf} self_mute={vs.self_mute} '
        f'deaf={vs.deaf} mute={vs.mute} '
        f'self_stream={vs.self_stream} self_video={vs.self_video} '
        f'suppress={vs.suppress} afk={vs.afk} '
        f'request_to_speak={rts.isoformat() if rts else None}'
        f')'
    )


def format_discord_voice_client(vc: discord.VoiceProtocol | None) -> str:
    """discord.voice.client.VoiceClient fields that explain transport/session health."""
    if vc is None:
        return 'VoiceClient=NULL'

    ch = vc.channel
    g = getattr(ch, 'guild', None) if ch is not None else None
    try:
        conn = vc.is_connected()
    except Exception as e:
        conn = f'<is_connected_error {type(e).__name__}>'
    base = (
        f'VoiceProtocol_obj_id={id(vc)} cls={vc.__class__.__name__} '
        f'is_connected={conn} '
        f'channel_snowflake_id={(ch.id if ch else None)} channel_obj_id={(id(ch) if ch else None)} '
        f'guild_id={(g.id if g else None)}'
    )

    if not isinstance(vc, discord.VoiceClient):
        return base + ' | (not discord.VoiceClient; no session/endpoint)'

    try:
        ws = vc.ws
        ws_ok = getattr(ws, 'open', None)
        close_code = getattr(ws, '_close_code', None)
        ws_bits = f'ws_open={ws_ok} ws_close_code={close_code}'
    except Exception as e:
        ws_bits = f'ws_err={type(e).__name__}:{e}'

    latency = vc.latency
    avg_latency = vc.average_latency

    endpoint = vc.endpoint

    playing = paused = False
    try:
        playing = vc.is_playing()
        paused = vc.is_paused()
    except Exception:
        pass

    return (
        f'{base} | '
        f'session_id={vc.session_id!r} '
        f'endpoint={endpoint!r} '
        f'mode={vc.mode!r} '
        f'ssrc={vc.ssrc} '
        f'dave={vc.is_dave_connection()} '
        f'latency_sec={latency} avg_latency_sec={avg_latency} '
        f'playing={playing} paused={paused} | {ws_bits}'
    )


def format_member_voice(member: discord.Member | None, label: str = 'member') -> str:
    if member is None:
        return f'{label}=<None>'
    return (
        f'{label}(member_obj_id={id(member)} user_id={member.id} nick={member.nick!r} '
        f'{format_discord_voice_state(member.voice, label + "_vs")})'
    )


def format_guild_voice_snapshot(guild: discord.Guild, label: str = 'guild_vc') -> str:
    """Full picture: VoiceClient transport + cached Member.voice for the logged-in bot."""
    me = guild.me
    return (
        f'{label}[guild_snowflake_id={guild.id} guild_obj_id={id(guild)}] '
        f'{format_discord_voice_client(guild.voice_client)} '
        f'| {format_member_voice(me, "bot_member")}'
    )


def format_follower_voice_slots(guild: discord.Guild, user_ids: list[int]) -> str:
    """
    Voice state *as seen by discord.py caches* for user ids (e.g. /follow targets).
    Logs <member_not_in_cache> when the gateway saw a voice user we cannot resolve yet.
    """
    parts = []
    for uid in sorted(user_ids):
        m = guild.get_member(uid)
        if m is None:
            parts.append(f'{uid}=<member_not_in_cache>')
        else:
            parts.append(format_member_voice(m, f'follow_uid_{uid}'))
    return '[ ' + '; '.join(parts) + ' ]'
