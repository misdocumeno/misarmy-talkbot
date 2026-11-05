"""Stall watcher alerts without mutating playback."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.observability.metrics import MetricsRegistry
from misarmy_talkbot.observability.stall import StallWatcher
from tests.helpers import ready_audio_message, reset_singletons


@pytest.mark.asyncio
async def test_stall_watcher_logs_frozen_progress() -> None:
    reset_singletons()
    metrics = MetricsRegistry.instance()
    watcher = StallWatcher()
    watcher._no_progress_sec = 0.01
    watcher._interval = 0.05

    engine = MagicMock()
    engine.note_progress_for_stall.return_value = 5.0
    engine.last_position_ms = 100
    engine.current_track_length_ms = 10_000
    engine.queue.head.return_value = None
    engine.has_gen_task_for.return_value = False

    session = MagicMock()
    session.guild_id = 99
    session.engine = engine
    registry = MagicMock()
    registry.iter_alive.return_value = [session]

    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.12)
        stop.set()

    with patch(
        'misarmy_talkbot.observability.stall.GuildSessionRegistry.instance',
        return_value=registry,
    ):
        asyncio.create_task(stop_soon())
        await watcher.run(stop)
    assert metrics._counters.get('stall_detected_total', {}).get(99, 0) >= 1


@pytest.mark.asyncio
async def test_processing_head_without_gen_task_alerts() -> None:
    reset_singletons()
    metrics = MetricsRegistry.instance()
    watcher = StallWatcher()
    watcher._tts_zero_progress_sec = 0.01
    watcher._interval = 0.05
    watcher._no_progress_sec = 9999

    audio = ready_audio_message()
    audio.state = AudioState.PROCESSING
    audio.timestamp = time.time() - 10

    engine = MagicMock()
    engine.note_progress_for_stall.return_value = None
    engine.queue.head.return_value = audio
    engine.has_gen_task_for.return_value = False

    session = MagicMock()
    session.guild_id = 42
    session.engine = engine
    registry = MagicMock()
    registry.iter_alive.return_value = [session]

    stop = asyncio.Event()

    async def stop_soon() -> None:
        await asyncio.sleep(0.12)
        stop.set()

    with patch(
        'misarmy_talkbot.observability.stall.GuildSessionRegistry.instance',
        return_value=registry,
    ):
        asyncio.create_task(stop_soon())
        await watcher.run(stop)
    assert metrics._counters.get('stall_detected_total', {}).get(42, 0) >= 1
