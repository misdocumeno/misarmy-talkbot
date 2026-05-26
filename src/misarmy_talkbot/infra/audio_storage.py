"""Transient TTS audio files on tmpfs, shared with the Lavalink container.

Lavalink's local source loads tracks from an absolute filesystem path (same path
inside both containers). The bot writes one MP3 per ``AudioMessage`` to the
shared tmpfs; the engine deletes the file once Lavalink reports the track ended.
A best-effort TTL janitor cleans up files orphaned by crashes so the tmpfs cannot
grow unbounded.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import aiofiles

from misarmy_talkbot.observability.logger import logger


class AudioStorage:
    """Per-process owner of the tmpfs audio directory.

    The directory is shared with the Lavalink container; do not point this at a
    real disk in production unless you know the I/O cost is acceptable.
    """

    _instance: AudioStorage | None = None

    def __init__(self, audio_dir: Path | None = None) -> None:
        path = audio_dir or Path(os.getenv('AUDIO_DIR', '/tmp/talkbot-audio'))
        path.mkdir(parents=True, exist_ok=True)
        self._dir = path
        self._max_age_s = float(os.getenv('AUDIO_TTL_SECONDS', '600'))

    @classmethod
    def instance(cls) -> AudioStorage:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def directory(self) -> Path:
        return self._dir

    async def write(self, payload: bytes) -> Path:
        """Write ``payload`` to a uniquely named MP3 and return the absolute path."""
        path = self._dir / f'{uuid.uuid4().hex}.mp3'
        async with aiofiles.open(path, 'wb') as fh:
            await fh.write(payload)
        return path

    async def delete(self, path: Path | None) -> None:
        """Best-effort unlink; missing files and tmpfs hiccups are not errors."""
        if path is None:
            return
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except OSError:
            logger.warning('audio_storage_delete_failed path=%s', path)

    async def janitor_loop(self, stop_event: asyncio.Event) -> None:
        """Drop files older than ``AUDIO_TTL_SECONDS``; runs until ``stop_event``.

        This is a safety net for crashes between ``write`` and ``delete``: under
        normal lifecycle the engine deletes files on track-end. The interval is
        long because tmpfs is memory and the typical file is short-lived.
        """
        interval_s = float(os.getenv('AUDIO_JANITOR_INTERVAL_SECONDS', '120'))
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except TimeoutError:
                await self._sweep_once()

    async def _sweep_once(self) -> None:
        cutoff = time.time() - self._max_age_s
        try:
            entries = await asyncio.to_thread(list, self._dir.iterdir())
        except OSError:
            return
        removed = 0
        for entry in entries:
            try:
                if not entry.is_file():
                    continue
                if entry.stat().st_mtime > cutoff:
                    continue
                entry.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        if removed:
            logger.info('audio_storage_janitor_swept removed=%s', removed)

    def lavalink_identifier(self, path: Path) -> str:
        """Return the load identifier Lavalink's local source expects."""
        return path.as_posix()
