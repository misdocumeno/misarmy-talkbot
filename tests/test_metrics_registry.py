"""Sanity check for process-level counters (used for gateway ready / resume tallies)."""

from misarmy_talkbot.observability.metrics import MetricsRegistry


def test_inc_process() -> None:
    MetricsRegistry._instance = None
    registry = MetricsRegistry.instance()
    registry.inc_process('on_ready_count')
    registry.inc_process('on_ready_count', 2)
    assert registry._process['on_ready_count'] == 3
    MetricsRegistry._instance = None
