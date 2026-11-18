"""Boot-time measurement: per-phase durations, time-to-ready, time-to-first-audio.

The clock starts at import time of this module, which ``__main__`` imports
before anything heavy, so the ``imports_app`` phase covers the cost of pulling
in discord/wavelink/aiohttp.

Each phase is kept as wall-clock nanoseconds so that, once ready, the whole boot
can be emitted as an OpenTelemetry root span ``startup`` with one child span per
phase (retroactive, so it works even though tracing is configured mid-boot).
Phases are also observed into a Prometheus histogram for dashboards.

Phases that finish after ready (e.g. background slash sync) are still observed
into the histogram and logged, but are not part of the ready-time span tree.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

_PROCESS_START_MONO = time.monotonic()
_PROCESS_START_NS = time.time_ns()

_PHASE_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120)


@dataclass(frozen=True)
class PhaseRecord:
    name: str
    start_ns: int
    end_ns: int

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / 1e9


class StartupTracker:
    """Singleton recording boot phases and the two headline gauges."""

    _instance: StartupTracker | None = None

    def __init__(
        self,
        *,
        clock_ns: Callable[[], int] = time.time_ns,
        mono: Callable[[], float] = time.monotonic,
        process_start_ns: int = _PROCESS_START_NS,
        process_start_mono: float = _PROCESS_START_MONO,
    ) -> None:
        self._clock_ns = clock_ns
        self._mono = mono
        self._process_start_ns = process_start_ns
        self._process_start_mono = process_start_mono
        self._last_checkpoint_ns = process_start_ns
        self._open: dict[str, int] = {}
        self.phases: list[PhaseRecord] = []
        self.ready_at_s: float | None = None
        self.first_audio_at_s: float | None = None
        self._ready_span_names: set[str] = set()
        self._histogram: object | None = None
        self._ready_gauge: object | None = None
        self._first_audio_gauge: object | None = None
        self._init_prometheus()

    @classmethod
    def instance(cls) -> StartupTracker:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- prometheus wiring -------------------------------------------------

    def _init_prometheus(self) -> None:
        try:
            from prometheus_client import Gauge, Histogram
        except ImportError:
            return
        try:
            self._histogram = Histogram(
                'talkbot_startup_phase_seconds',
                'Duration of each boot phase in seconds',
                ['phase'],
                buckets=_PHASE_BUCKETS,
            )
            self._ready_gauge = Gauge(
                'talkbot_time_to_ready_seconds',
                'Seconds from process start until boot finished (ready to serve)',
            )
            self._first_audio_gauge = Gauge(
                'talkbot_time_to_first_audio_seconds',
                'Seconds from process start until the first track started playing',
            )
        except ValueError:
            # Already registered (e.g. module reloaded in tests). Reuse the
            # default registry's collectors instead of failing boot.
            self._histogram = None
            self._ready_gauge = None
            self._first_audio_gauge = None

    # ---- recording ---------------------------------------------------------

    def elapsed_s(self) -> float:
        return self._mono() - self._process_start_mono

    def checkpoint(self, name: str) -> PhaseRecord:
        """Close a phase that started at the previous checkpoint (or process start)."""
        now = self._clock_ns()
        record = PhaseRecord(name, self._last_checkpoint_ns, now)
        self._last_checkpoint_ns = now
        self._store(record)
        return record

    def begin(self, name: str) -> None:
        self._open[name] = self._clock_ns()

    def end(self, name: str) -> PhaseRecord | None:
        """Close a phase opened with ``begin``. Idempotent: second call is a no-op."""
        start = self._open.pop(name, None)
        if start is None:
            return None
        record = PhaseRecord(name, start, self._clock_ns())
        self._store(record)
        return record

    @contextmanager
    def phase(self, name: str) -> Generator[None]:
        self.begin(name)
        try:
            yield
        finally:
            self.end(name)

    def _store(self, record: PhaseRecord) -> None:
        self.phases.append(record)
        if self.ready_at_s is None:
            self._ready_span_names.add(record.name)
        if self._histogram is not None:
            self._histogram.labels(phase=record.name).observe(  # type: ignore[attr-defined]
                record.duration_s
            )
        logger.info(
            'startup_phase phase=%s seconds=%.3f',
            record.name,
            record.duration_s,
        )

    # ---- headline milestones ----------------------------------------------

    def mark_ready(self) -> None:
        """Boot finished: the bot can serve commands and accept speak requests."""
        if self.ready_at_s is not None:
            return
        self.ready_at_s = self.elapsed_s()
        if self._ready_gauge is not None:
            self._ready_gauge.set(self.ready_at_s)  # type: ignore[attr-defined]
        ready_ns = self._clock_ns()
        self._emit_spans(ready_ns)
        summary = ' '.join(
            f'{p.name}={p.duration_s:.3f}s'
            for p in self.phases
            if p.name in self._ready_span_names
        )
        logger.info(
            'startup_ready time_to_ready=%.3fs phases: %s',
            self.ready_at_s,
            summary,
        )

    def mark_first_audio(self) -> None:
        """First track started. Only the first call counts."""
        if self.first_audio_at_s is not None:
            return
        self.first_audio_at_s = self.elapsed_s()
        if self._first_audio_gauge is not None:
            self._first_audio_gauge.set(self.first_audio_at_s)  # type: ignore[attr-defined]
        logger.info(
            'startup_first_audio time_to_first_audio=%.3fs',
            self.first_audio_at_s,
        )

    def _emit_spans(self, ready_ns: int) -> None:
        try:
            from opentelemetry import trace
        except ImportError:
            return
        tracer = trace.get_tracer('misarmy_talkbot.startup')
        root = tracer.start_span('startup', start_time=self._process_start_ns)
        ctx = trace.set_span_in_context(root)
        for record in self.phases:
            if record.name not in self._ready_span_names:
                continue
            child = tracer.start_span(
                record.name, context=ctx, start_time=record.start_ns
            )
            child.end(end_time=record.end_ns)
        root.end(end_time=ready_ns)


def startup_tracker() -> StartupTracker:
    return StartupTracker.instance()
