"""Watch playback/TTS progress and emit alerts without auto-skipping audio."""

from __future__ import annotations

import asyncio
import os
import time

from misarmy_talkbot.core.playback.audio import AudioState
from misarmy_talkbot.core.session.registry import GuildSessionRegistry
from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.metrics import MetricsRegistry


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class StallWatcher:
    """Periodic scan of live guild engines for frozen Lavalink progress / TTS."""

    def __init__(self) -> None:
        self._no_progress_sec = _env_float('STALL_NO_PROGRESS_SEC', 30.0)
        self._tts_zero_progress_sec = _env_float(
            'STALL_TTS_ZERO_PROGRESS_SEC', 60.0
        )
        self._interval = _env_float('STALL_POLL_INTERVAL_SEC', 10.0)
        self._last_alert: dict[tuple[int, str], float] = {}

    async def run(self, stop_event: asyncio.Event) -> None:
        metrics = MetricsRegistry.instance()
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._interval
                )
                return
            except TimeoutError:
                pass
            for session in GuildSessionRegistry.instance().iter_alive():
                engine = session.engine
                guild_id = session.guild_id
                stuck_for = engine.note_progress_for_stall()
                if stuck_for is not None:
                    metrics.set_gauge(
                        'speak_blocked_seconds', guild_id, stuck_for
                    )
                    if stuck_for >= self._no_progress_sec:
                        self._alert(
                            guild_id,
                            'playback_no_progress',
                            stuck_for=stuck_for,
                            position_ms=engine.last_position_ms,
                            track_length_ms=engine.current_track_length_ms,
                        )
                else:
                    metrics.set_gauge('speak_blocked_seconds', guild_id, 0.0)

                head = engine.queue.head()
                if (
                    head is not None
                    and head.state == AudioState.PROCESSING
                    and not engine.has_gen_task_for(head.original.id)
                ):
                    age = max(0.0, time.time() - head.timestamp)
                    if age >= self._tts_zero_progress_sec:
                        self._alert(
                            guild_id,
                            'tts_processing_no_task',
                            age_s=age,
                            message_id=head.original.id,
                        )
                elif (
                    head is not None
                    and head.state == AudioState.PROCESSING
                    and engine.has_gen_task_for(head.original.id)
                ):
                    age = max(0.0, time.time() - head.timestamp)
                    if age >= self._tts_zero_progress_sec:
                        self._alert(
                            guild_id,
                            'tts_zero_progress',
                            age_s=age,
                            message_id=head.original.id,
                        )

    def _alert(self, guild_id: int, kind: str, **fields: object) -> None:
        key = (guild_id, kind)
        now = time.monotonic()
        last = self._last_alert.get(key, 0.0)
        if now - last < 60.0:
            return
        self._last_alert[key] = now
        MetricsRegistry.instance().inc('stall_detected_total', guild_id)
        logger.warning(
            'stall_detected guild_id=%s kind=%s %s',
            guild_id,
            kind,
            ' '.join(f'{k}={v!r}' for k, v in fields.items()),
        )


_stall_stop: asyncio.Event | None = None


def stall_stop_event() -> asyncio.Event:
    global _stall_stop
    if _stall_stop is None:
        _stall_stop = asyncio.Event()
    return _stall_stop
