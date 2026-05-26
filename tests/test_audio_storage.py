"""AudioStorage write/delete + janitor sweep behavior."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from misarmy_talkbot.infra.audio_storage import AudioStorage


@pytest.mark.asyncio
async def test_write_and_delete_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = AudioStorage(audio_dir=Path(tmp))
        path = await storage.write(b'hello')
        assert path.exists()
        assert path.read_bytes() == b'hello'
        await storage.delete(path)
        assert not path.exists()


@pytest.mark.asyncio
async def test_delete_idempotent_for_missing_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = AudioStorage(audio_dir=Path(tmp))
        await storage.delete(Path(tmp) / 'never-existed.mp3')
        await storage.delete(None)


@pytest.mark.asyncio
async def test_janitor_removes_old_files_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['AUDIO_TTL_SECONDS'] = '0.5'
        os.environ['AUDIO_JANITOR_INTERVAL_SECONDS'] = '0.05'
        try:
            storage = AudioStorage(audio_dir=Path(tmp))
            old = await storage.write(b'old')
            old_mtime = time.time() - 5.0
            os.utime(old, (old_mtime, old_mtime))
            recent = await storage.write(b'recent')
            stop = asyncio.Event()
            task = asyncio.create_task(storage.janitor_loop(stop))
            await asyncio.sleep(0.15)
            stop.set()
            await asyncio.wait_for(task, timeout=1.0)
            assert not old.exists()
            assert recent.exists()
        finally:
            os.environ.pop('AUDIO_TTL_SECONDS', None)
            os.environ.pop('AUDIO_JANITOR_INTERVAL_SECONDS', None)


def test_lavalink_identifier_is_absolute_posix_path() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        storage = AudioStorage(audio_dir=Path(tmp))
        path = Path(tmp) / 'foo.mp3'
        assert storage.lavalink_identifier(path) == path.as_posix()
        assert storage.lavalink_identifier(path).startswith('/')
