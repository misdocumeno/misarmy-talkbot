"""Readiness probe used by GET /healthz (and the compose healthcheck)."""

from misarmy_talkbot.observability import health


def test_unhealthy_until_probe_is_set() -> None:
    health._probe = None
    assert health.is_healthy() is False


def test_probe_result_is_reported() -> None:
    health.set_health_probe(lambda: True)
    assert health.is_healthy() is True
    health.set_health_probe(lambda: False)
    assert health.is_healthy() is False


def test_probe_exception_counts_as_unhealthy() -> None:
    def boom() -> bool:
        raise RuntimeError('gateway gone')

    health.set_health_probe(boom)
    assert health.is_healthy() is False
    health._probe = None
