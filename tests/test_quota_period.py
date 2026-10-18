"""TTS soft-cap quota period boundaries."""

from __future__ import annotations

from datetime import UTC, datetime

from misarmy_talkbot.core.playback.tts.quota_period import quota_period_start


def test_default_is_first_of_utc_month() -> None:
    now = datetime(2026, 10, 17, 15, 0, tzinfo=UTC)
    start = quota_period_start(now=now)
    assert start == datetime(2026, 10, 1, tzinfo=UTC)


def test_mid_month_reset_still_in_period() -> None:
    now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    start = quota_period_start(month_day=15, now=now)
    assert start == datetime(2026, 9, 15, tzinfo=UTC)


def test_mid_month_reset_after_boundary() -> None:
    now = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    start = quota_period_start(month_day=15, now=now)
    assert start == datetime(2026, 10, 15, tzinfo=UTC)


def test_january_wraps_to_previous_year() -> None:
    now = datetime(2026, 1, 5, tzinfo=UTC)
    start = quota_period_start(month_day=10, now=now)
    assert start == datetime(2025, 12, 10, tzinfo=UTC)
