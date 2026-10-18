"""Quota month boundaries for TTS soft caps (provider billing cycles)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from datetime import tzinfo


def _resolve_tz(tz_name: str) -> tzinfo:
    # ZoneInfo('UTC') needs the tzdata package on some Windows installs.
    if not tz_name or tz_name.strip().upper() in {'UTC', 'GMT'}:
        return UTC
    try:
        return ZoneInfo(tz_name.strip())
    except Exception:
        return UTC


def quota_period_start(
    *,
    month_day: int = 1,
    tz_name: str = 'UTC',
    now: datetime | None = None,
) -> datetime:
    """Start of the current quota period for a provider.

    Providers bill on a calendar-month-like cycle that may start on a day other
    than the 1st (Azure Speech F0 shows the exact reset under Pricing tier).
    ``month_day`` is clamped to 1..28 so February never overflows.
    """
    day = max(1, min(28, int(month_day)))
    tz = _resolve_tz(tz_name)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    if current.day >= day:
        return datetime(current.year, current.month, day, tzinfo=tz)
    if current.month == 1:
        return datetime(current.year - 1, 12, day, tzinfo=tz)
    return datetime(current.year, current.month - 1, day, tzinfo=tz)
