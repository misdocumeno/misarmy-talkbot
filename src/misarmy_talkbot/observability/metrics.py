"""In-process metrics + Prometheus dual-write for operator visibility."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

_prom_counters: dict[str, Any] = {}
_prom_gauges: dict[str, Any] = {}
_prom_ready = False


def _ensure_prom() -> bool:
    global _prom_ready
    if _prom_ready:
        return True
    try:
        from prometheus_client import Counter, Gauge

        # Lazy registry: create labeled metrics on first use via helpers below.
        _prom_counters['__Counter'] = Counter
        _prom_gauges['__Gauge'] = Gauge
        _prom_ready = True
        return True
    except Exception:
        return False


def _prom_counter(key: str) -> Any | None:
    if not _ensure_prom():
        return None
    counter = _prom_counters.get(key)
    if counter is None:
        Counter = _prom_counters['__Counter']
        counter = Counter(
            f'talkbot_{key}',
            f'Talkbot counter {key}',
            ['guild_id'],
        )
        _prom_counters[key] = counter
    return counter


def _prom_gauge(key: str) -> Any | None:
    if not _ensure_prom():
        return None
    gauge = _prom_gauges.get(key)
    if gauge is None:
        Gauge = _prom_gauges['__Gauge']
        gauge = Gauge(
            f'talkbot_{key}',
            f'Talkbot gauge {key}',
            ['guild_id'],
        )
        _prom_gauges[key] = gauge
    return gauge


class MetricsRegistry:
    """Guild-scoped counters/gauges mirrored to Prometheus when available."""

    _instance: MetricsRegistry | None = None

    def __init__(self) -> None:
        def _guild_int_counter() -> defaultdict[int, int]:
            return defaultdict(int)

        def _guild_float_gauge() -> defaultdict[int, float]:
            return defaultdict(float)

        self._counters: defaultdict[str, defaultdict[int, int]] = defaultdict(
            _guild_int_counter
        )
        self._gauges: defaultdict[str, defaultdict[int, float]] = defaultdict(
            _guild_float_gauge
        )
        self._process: dict[str, float | int] = {}

    @classmethod
    def instance(cls) -> MetricsRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def inc(self, key: str, guild_id: int, n: int = 1) -> None:
        self._counters[key][guild_id] += int(n)
        counter = _prom_counter(key)
        if counter is not None:
            counter.labels(guild_id=str(guild_id)).inc(n)

    def set_gauge(self, key: str, guild_id: int, value: float) -> None:
        self._gauges[key][guild_id] = float(value)
        gauge = _prom_gauge(key)
        if gauge is not None:
            gauge.labels(guild_id=str(guild_id)).set(float(value))

    def set_process(self, key: str, value: float | int) -> None:
        self._process[key] = value

    def inc_process(self, key: str, n: int = 1) -> None:
        current = int(self._process.get(key, 0))
        self._process[key] = current + int(n)

    def snapshot_guild_embed_fields(
        self, guild_id: int
    ) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for key in sorted(self._counters):
            if guild_id in self._counters[key]:
                rows.append((key, str(self._counters[key][guild_id])))
        for key in sorted(self._gauges):
            if guild_id in self._gauges[key]:
                rows.append((
                    f'{key} (gauge)',
                    f'{self._gauges[key][guild_id]:.2f}',
                ))
        return [(name[:256], value[:1024]) for name, value in rows[:24]]

    async def snapshot_loop_task(
        self, stop_event: asyncio.Event, interval_sec: float
    ) -> None:
        from misarmy_talkbot.observability.logger import logger

        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
            except TimeoutError:
                task_count = len(asyncio.all_tasks())
                self.set_process('tasks_sample', task_count)
                logger.debug('metrics_tick tasks_sample=%s', task_count)


def snapshot_for_logging(guild_id: int) -> dict[str, Any]:
    registry = MetricsRegistry.instance()
    return {
        'counters': {
            k: dict(registry._counters[k])
            for k in registry._counters
            if guild_id in registry._counters[k]
        },
        'gauges': {
            k: dict(registry._gauges[k])
            for k in registry._gauges
            if guild_id in registry._gauges[k]
        },
    }
