"""Readiness probe behind ``GET /healthz`` (served on the metrics HTTP port).

"Healthy" means the bot finished boot and holds a live gateway session. The
deploy script and the compose healthcheck both key off this, so it must only
flip to healthy once the bot can actually serve commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_probe: Callable[[], bool] | None = None


def set_health_probe(probe: Callable[[], bool]) -> None:
    global _probe
    _probe = probe


def is_healthy() -> bool:
    if _probe is None:
        return False
    try:
        return bool(_probe())
    except Exception:  # noqa: BLE001 - a broken probe must read as unhealthy
        return False
