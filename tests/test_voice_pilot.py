"""Voice pilot recovery policy and health gating (mocked connect path).

These tests drive close-code handling directly because production failures show up as
wrong reconnect timing or a stuck ``wait_until_healthy`` loop, not as bad slash-command text.
"""

from __future__ import annotations

import asyncio

import pytest

from misarmy_talkbot.core.voice.pilot import VoicePilot
from tests.helpers import FakeBot, FakeVoiceClient


@pytest.fixture
def pilot(monkeypatch: pytest.MonkeyPatch) -> tuple[VoicePilot, list[str]]:
    """Pilot with ``_force_fresh_connect`` recorded instead of touching Discord."""
    reasons: list[str] = []

    async def record_force(_self: VoicePilot, reason: str) -> None:
        reasons.append(reason)

    monkeypatch.setattr(VoicePilot, '_force_fresh_connect', record_force)
    monkeypatch.setenv('WS_4014_WAIT_MS', '50')
    monkeypatch.setenv('WS_LIBRARY_GRACE_MS', '10')
    monkeypatch.setenv('WS_RATE_LIMIT_BACKOFF_MS', '10')
    bot = FakeBot(guild_id=42)
    voice_pilot = VoicePilot(bot, 42)  # type: ignore[arg-type]
    voice_pilot.intended_channel_id = 100
    voice_client = FakeVoiceClient(connected=True)
    voice_client.channel.id = 100
    voice_pilot._voice_client = voice_client  # type: ignore[assignment]
    return voice_pilot, reasons


@pytest.mark.asyncio
async def test_is_healthy_clears_stale_gateway_transient_when_voice_linked(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, _ = pilot
    voice_pilot.set_gateway_transient(True)
    assert voice_pilot.is_healthy() is True
    assert voice_pilot.gateway_transient is False


@pytest.mark.asyncio
async def test_wait_until_healthy_exits_when_gateway_transient_cleared(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, _ = pilot
    voice_pilot.set_gateway_transient(True)

    async def clear_transient() -> None:
        await asyncio.sleep(0.05)
        voice_pilot.set_gateway_transient(False)

    asyncio.create_task(clear_transient())
    await asyncio.wait_for(voice_pilot.wait_until_healthy(), timeout=1.0)


@pytest.mark.asyncio
async def test_close_1000_does_not_force_reconnect(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, reasons = pilot
    await voice_pilot._handle_close_code(1000)
    assert reasons == []


@pytest.mark.asyncio
async def test_close_4006_forces_fresh_connect(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, reasons = pilot
    await voice_pilot._handle_close_code(4006)
    assert reasons == ['close_4006']


@pytest.mark.asyncio
async def test_close_4014_with_server_signal_reconnects(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, reasons = pilot
    voice_pilot._server_signal.set()
    await voice_pilot._handle_close_code(4014)
    assert 'close_4014' in reasons


@pytest.mark.asyncio
async def test_close_4014_timeout_still_reconnects(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, reasons = pilot
    voice_pilot._server_signal.clear()
    await voice_pilot._handle_close_code(4014)
    assert 'close_4014' in reasons


@pytest.mark.asyncio
async def test_close_4017_marks_fatal_and_unhealthy(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, reasons = pilot
    await voice_pilot._handle_close_code(4017)
    assert voice_pilot._fatal_voice is True
    assert voice_pilot.is_healthy() is False
    assert reasons == []


@pytest.mark.asyncio
async def test_pilot_identity_stable_across_close_codes(
    pilot: tuple[VoicePilot, list[str]],
) -> None:
    voice_pilot, _ = pilot
    pilot_id = id(voice_pilot)
    await voice_pilot._handle_close_code(4006)
    await voice_pilot._handle_close_code(4009)
    assert id(voice_pilot) == pilot_id
