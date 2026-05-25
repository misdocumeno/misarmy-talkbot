"""MisarmyVoiceClient close-code observer behavior.

Intentional pilot disconnects must not enqueue spurious close codes that would trigger a
second recovery pass (production symptom: reconnect loops).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from misarmy_talkbot.core.voice.voice_client import MisarmyVoiceClient


@pytest.mark.asyncio
async def test_suppress_observer_skips_close_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[int] = asyncio.Queue()
    client = MisarmyVoiceClient(MagicMock(), MagicMock())
    client._close_observer = queue
    client.set_suppress_close_observer(True)

    async def fake_super_disconnect(*_args: object, **_kwargs: object) -> None:
        return

    monkeypatch.setattr(
        'misarmy_talkbot.core.voice.voice_client.VoiceClient.disconnect',
        fake_super_disconnect,
    )
    await client.disconnect(force=True)
    assert queue.empty()


@pytest.mark.asyncio
async def test_observer_receives_close_code_when_not_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue: asyncio.Queue[int] = asyncio.Queue()
    client = MisarmyVoiceClient(MagicMock(), MagicMock())
    client._close_observer = queue
    mock_ws = MagicMock()
    mock_ws.close_code = 4009

    async def fake_super_disconnect(*_args: object, **_kwargs: object) -> None:
        return

    monkeypatch.setattr(
        'misarmy_talkbot.core.voice.voice_client.VoiceClient.disconnect',
        fake_super_disconnect,
    )
    with patch.object(
        type(client), 'ws', new_callable=PropertyMock, return_value=mock_ws
    ):
        await client.disconnect(force=True)
    assert await asyncio.wait_for(queue.get(), timeout=0.5) == 4009
