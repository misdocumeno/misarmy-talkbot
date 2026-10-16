"""In-memory HTTP audio bridge used by Lavalink."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from aiohttp import ClientSession

from misarmy_talkbot.infra.audio_http import AudioHttpServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def server() -> AsyncIterator[AudioHttpServer]:
    AudioHttpServer.reset()
    http = AudioHttpServer(
        host='127.0.0.1',
        port=0,
        public_base='http://127.0.0.1',
    )
    await http.start()
    assert http._site is not None
    sockets = http._site._server.sockets  # type: ignore[union-attr]
    assert sockets
    port = sockets[0].getsockname()[1]
    http._port = port
    http._public_base = f'http://127.0.0.1:{port}'
    AudioHttpServer._instance = http
    yield http
    await http.stop()
    AudioHttpServer.reset()


@pytest.mark.asyncio
async def test_publish_complete_and_get(server: AudioHttpServer) -> None:
    payload = b'\xff\xfb' + b'\x00' * 64
    session = await server.publish_complete(payload)
    url = server.url_for(session.stream_id)
    async with ClientSession() as client:
        async with client.get(url) as response:
            assert response.status == 200
            assert response.headers['Content-Type'] == 'audio/mpeg'
            body = await response.read()
    assert body == payload


@pytest.mark.asyncio
async def test_chunked_stream(server: AudioHttpServer) -> None:
    session = server.create_session()
    url = server.url_for(session.stream_id)

    async def feed() -> None:
        await asyncio.sleep(0.01)
        await server.push(session, b'aaa')
        await server.push(session, b'bbb')
        await server.close_session(session)

    feeder = asyncio.create_task(feed())
    async with ClientSession() as client:
        async with client.get(url) as response:
            body = await response.read()
    await feeder
    assert body == b'aaabbb'
