"""Startup phase tracker: checkpoints, idempotent end, ready/first-audio milestones."""

from __future__ import annotations

from misarmy_talkbot.observability.startup import StartupTracker


class FakeClock:
    def __init__(self) -> None:
        self.ns = 1_000_000_000
        self.mono = 0.0

    def time_ns(self) -> int:
        return self.ns

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1e9)
        self.mono += seconds


def _tracker(clock: FakeClock) -> StartupTracker:
    return StartupTracker(
        clock_ns=clock.time_ns,
        mono=clock.monotonic,
        process_start_ns=clock.ns,
        process_start_mono=clock.mono,
    )


def test_checkpoint_measures_from_previous_boundary() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    clock.advance(0.5)
    first = tracker.checkpoint('config')
    clock.advance(1.25)
    second = tracker.checkpoint('imports_app')

    assert first.name == 'config'
    assert round(first.duration_s, 3) == 0.5
    assert second.name == 'imports_app'
    assert round(second.duration_s, 3) == 1.25


def test_begin_end_records_phase_and_end_is_idempotent() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    tracker.begin('db_open')
    clock.advance(0.2)
    record = tracker.end('db_open')
    clock.advance(5)
    again = tracker.end('db_open')

    assert record is not None
    assert round(record.duration_s, 3) == 0.2
    assert again is None
    assert [p.name for p in tracker.phases] == ['db_open']


def test_phase_context_manager_closes_on_exception() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    try:
        with tracker.phase('guild_config'):
            clock.advance(0.1)
            raise RuntimeError('boom')
    except RuntimeError:
        pass
    assert [p.name for p in tracker.phases] == ['guild_config']


def test_mark_ready_sets_time_and_is_once_only() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    clock.advance(3.0)
    tracker.mark_ready()
    clock.advance(10.0)
    tracker.mark_ready()

    assert tracker.ready_at_s == 3.0


def test_mark_first_audio_only_first_call_counts() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    clock.advance(7.5)
    tracker.mark_first_audio()
    clock.advance(20)
    tracker.mark_first_audio()

    assert tracker.first_audio_at_s == 7.5


def test_phases_finishing_after_ready_are_recorded_but_not_in_ready_set() -> (
    None
):
    clock = FakeClock()
    tracker = _tracker(clock)
    tracker.begin('command_sync')
    clock.advance(1.0)
    tracker.mark_ready()
    clock.advance(2.0)
    tracker.end('command_sync')

    assert 'command_sync' not in tracker._ready_span_names
    assert [p.name for p in tracker.phases] == ['command_sync']
    assert round(tracker.phases[0].duration_s, 3) == 3.0
