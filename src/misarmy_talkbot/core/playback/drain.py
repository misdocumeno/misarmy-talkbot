"""Drain state and the declarative snapshot payload (text and metadata only).

A drain lets the bot restart without a graceful voice teardown: playback of the
item currently playing in each guild finishes, nothing new starts, and the
pending state is written down so a fresh process can restore it.

The snapshot is plain data: guild ids, the voice channel, follow state and
references to pending Discord messages. It never contains audio bytes, WAV
files, or pickled Python objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SNAPSHOT_VERSION = 1


class SnapshotError(ValueError):
    """Snapshot payload is unreadable or has an unsupported shape/version."""


class DrainController:
    """Process-wide flag set by SIGUSR1. Requesting twice is a no-op."""

    _instance: DrainController | None = None

    def __init__(self) -> None:
        self._requested = False

    @classmethod
    def instance(cls) -> DrainController:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def requested(self) -> bool:
        return self._requested

    def request(self) -> bool:
        """Return True only for the first request."""
        if self._requested:
            return False
        self._requested = True
        return True

    def reset(self) -> None:
        self._requested = False


@dataclass(frozen=True)
class PendingItem:
    """One queued text message, referenced by Discord ids (not audio)."""

    message_id: int
    channel_id: int
    author_id: int
    text: str


@dataclass(frozen=True)
class IgnoredChannels:
    user_id: int
    channel_ids: tuple[int, ...]


@dataclass(frozen=True)
class GuildDrainState:
    guild_id: int
    voice_channel_id: int | None
    master_user_id: int | None
    follower_ids: tuple[int, ...]
    ignored: tuple[IgnoredChannels, ...]
    pending: tuple[PendingItem, ...]


def _guild_to_dict(state: GuildDrainState) -> dict[str, Any]:
    return {
        'guild_id': state.guild_id,
        'voice_channel_id': state.voice_channel_id,
        'master_user_id': state.master_user_id,
        'follower_ids': list(state.follower_ids),
        'ignored': [
            {'user_id': entry.user_id, 'channel_ids': list(entry.channel_ids)}
            for entry in state.ignored
        ],
        'pending': [
            {
                'message_id': item.message_id,
                'channel_id': item.channel_id,
                'author_id': item.author_id,
                'text': item.text,
            }
            for item in state.pending
        ],
    }


def encode_snapshot(states: list[GuildDrainState]) -> str:
    """Serialize to a versioned JSON document (stored as one DB row)."""
    document = {
        'version': SNAPSHOT_VERSION,
        'guilds': [_guild_to_dict(state) for state in states],
    }
    return json.dumps(document, separators=(',', ':'), sort_keys=True)


def _int(value: object, field: str) -> int:
    # bool is a subclass of int; reject it explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SnapshotError(f'{field} must be an int, got {value!r}')
    return value


def _opt_int(value: object, field: str) -> int | None:
    return None if value is None else _int(value, field)


def _int_list(value: object, field: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise SnapshotError(f'{field} must be a list')
    return tuple(_int(item, field) for item in value)


def _guild_from_dict(raw: object) -> GuildDrainState:
    if not isinstance(raw, dict):
        raise SnapshotError('guild entry must be an object')
    pending_raw = raw.get('pending', [])
    ignored_raw = raw.get('ignored', [])
    if not isinstance(pending_raw, list) or not isinstance(ignored_raw, list):
        raise SnapshotError('pending and ignored must be lists')
    pending: list[PendingItem] = []
    for item in pending_raw:
        if not isinstance(item, dict):
            raise SnapshotError('pending entry must be an object')
        text = item.get('text', '')
        if not isinstance(text, str):
            raise SnapshotError('pending text must be a string')
        pending.append(
            PendingItem(
                message_id=_int(item.get('message_id'), 'message_id'),
                channel_id=_int(item.get('channel_id'), 'channel_id'),
                author_id=_int(item.get('author_id'), 'author_id'),
                text=text,
            )
        )
    ignored: list[IgnoredChannels] = []
    for entry in ignored_raw:
        if not isinstance(entry, dict):
            raise SnapshotError('ignored entry must be an object')
        ignored.append(
            IgnoredChannels(
                user_id=_int(entry.get('user_id'), 'user_id'),
                channel_ids=_int_list(entry.get('channel_ids'), 'channel_ids'),
            )
        )
    return GuildDrainState(
        guild_id=_int(raw.get('guild_id'), 'guild_id'),
        voice_channel_id=_opt_int(
            raw.get('voice_channel_id'), 'voice_channel_id'
        ),
        master_user_id=_opt_int(raw.get('master_user_id'), 'master_user_id'),
        follower_ids=_int_list(raw.get('follower_ids', []), 'follower_ids'),
        ignored=tuple(ignored),
        pending=tuple(pending),
    )


def decode_snapshot(raw: str) -> list[GuildDrainState]:
    """Parse and validate a payload. Raises ``SnapshotError`` on anything unexpected."""
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f'invalid JSON: {exc}') from exc
    if not isinstance(document, dict):
        raise SnapshotError('snapshot must be an object')
    if document.get('version') != SNAPSHOT_VERSION:
        raise SnapshotError(
            f'unsupported snapshot version {document.get("version")!r}'
        )
    guilds = document.get('guilds')
    if not isinstance(guilds, list):
        raise SnapshotError('guilds must be a list')
    return [_guild_from_dict(entry) for entry in guilds]
