"""
Forensic helpers for GuildReader / readers dict / asyncio _speak tasks.
Read-only introspection — no behavior changes.
"""

from __future__ import annotations

import asyncio
from typing import Any


def audit_speak_tasks_for_guild(guild_id: int) -> str:
    """Tasks named exactly speak-guild-<id> (one expected per live GuildSpeaker for that guild)."""
    hits: list[str] = []
    for t in asyncio.all_tasks():
        try:
            n = t.get_name()
        except Exception:
            continue
        if n == f'speak-guild-{guild_id}':
            hits.append(f'{n} asyncio_task_id={id(t)} done={t.done()} cancelled={t.cancelled()}')
    if len(hits) > 1:
        return f'speak_tasks_ABNORMAL guild_id={guild_id} count={len(hits)} {hits}'
    return f'speak_tasks guild_id={guild_id} count={len(hits)} {hits}'


def audit_all_named_speak_tasks() -> str:
    """Every task whose name starts with speak-guild- (detect orphans / duplicate names across guilds)."""
    hits: list[str] = []
    for t in asyncio.all_tasks():
        try:
            n = t.get_name()
        except Exception:
            continue
        if n.startswith('speak-guild-'):
            hits.append(f'{n} asyncio_task_id={id(t)} done={t.done()} cancelled={t.cancelled()}')
    return f'ALL_named_speak_tasks count={len(hits)} RAW_LIST={hits}'


def audit_asyncio_all_tasks_sample(limit: int = 80) -> str:
    """Raw sample of every asyncio task (name + id + done); total count always included."""
    tasks = list(asyncio.all_tasks())
    ntot = len(tasks)
    chunk = tasks[:limit]
    bits = [f'name={t.get_name()!r} id={id(t)} done={t.done()} cancelled={t.cancelled()}' for t in chunk]
    tail = f' TRUNCATED(+{ntot - limit} not shown)' if ntot > limit else ''
    return f'asyncio_all_tasks total={ntot} sample_first_{limit}={bits}{tail}'


def readers_keys_identity_raw(readers: dict[Any, Any]) -> str:
    """Exact Python dict keys as (id(guild_obj), guild.snowflake_id) — semantic id alone is not enough."""
    pairs: list[tuple[int, int | None]] = []
    for g_key in readers.keys():
        gid = getattr(g_key, 'id', None)
        pairs.append((id(g_key), gid))
    return f'readers_keys_RAW_LIST_obj_id_snowflake={pairs} readers_dict_obj_id={id(readers)} len={len(readers)}'


def readers_registry_table(readers: dict[Any, Any], label: str) -> str:
    """
    One line per dict entry: guild id, Python id() of the key Guild object, reader/speaker ids, following user ids.
    Also flags duplicate guild ids among keys (should be impossible if Guild.__eq__ is id-based).
    """
    rows: list[str] = []
    seen_gid: dict[int, int] = {}
    dup: list[str] = []
    for g_key, reader in readers.items():
        gid = getattr(g_key, 'id', None)
        kid = id(g_key)
        if gid is not None:
            if gid in seen_gid and seen_gid[gid] != kid:
                dup.append(f'guild_id={gid} key_obj_ids {seen_gid[gid]} vs {kid}')
            seen_gid[gid] = kid
        fol = sorted(m.id for m in getattr(reader, '_following', ()))
        raw_follow: list[str] = []
        for mk, fmem in getattr(reader, '_following', {}).items():
            raw_follow.append(
                f'(member_key_obj_id={id(mk)},user_id={mk.id},followed_wrap_obj_id={id(fmem)})'
            )
        sp = getattr(reader, '_speaker', None)
        st = getattr(sp, '_speak_task', None) if sp is not None else None
        st_bit = (
            f'speak_task_asyncio_id={id(st)} speak_task_done={st.done()}' if st is not None else 'speak_task=<None>'
        )
        rows.append(
            f'guild_snowflake_id={gid} guild_key_obj_id={kid} reader_obj_id={id(reader)} '
            f'speaker_obj_id={id(sp)} {st_bit} following_user_ids_sorted={fol} '
            f'_following_items_RAW={raw_follow}'
        )
    dup_s = f' DUPLICATE_KEY_ALERT={dup}' if dup else ''
    return f'REGISTRY[{label}] n={len(readers)}{dup_s} | ' + ' || '.join(rows)


async def log_reader_forensics(logger: Any, *, scope: str, readers: dict[Any, Any], guild_id: int) -> None:
    """Registry + speak-task audit (awaitable so it runs in the bot loop)."""
    reg = readers_registry_table(readers, scope)
    keys_raw = readers_keys_identity_raw(readers)
    spk = audit_speak_tasks_for_guild(guild_id)
    all_spk = audit_all_named_speak_tasks()
    task_sample = audit_asyncio_all_tasks_sample()
    logger.info(
        f'READER_FORENSICS scope={scope} guild_snowflake_id={guild_id} | {keys_raw} | {spk} | {all_spk} | {reg} | '
        f'{task_sample}'
    )
