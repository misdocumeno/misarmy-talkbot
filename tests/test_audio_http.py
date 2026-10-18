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
            assert (
                response.headers['Content-Type'] == 'application/octet-stream'
            )
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


@pytest.mark.asyncio
async def test_replay_for_lavalink_dual_get(server: AudioHttpServer) -> None:
    """lavaplayer probes with one GET, then plays with a second GET from byte 0."""
    payload = b'\xff\xfb' + b'\x00' * 64
    session = await server.publish_complete(payload)
    url = server.url_for(session.stream_id)
    async with ClientSession() as client:
        async with client.get(url) as probe:
            assert probe.status == 200
            assert await probe.read() == payload
        async with client.get(url) as play:
            assert play.status == 200
            assert play.headers.get('Content-Length') == str(len(payload))
            assert await play.read() == payload


@pytest.mark.asyncio
async def test_client_disconnect_during_stream_is_quiet(
    server: AudioHttpServer,
) -> None:
    """lavaplayer probe often closes early; must not raise on write."""
    session = server.create_session()
    url = server.url_for(session.stream_id)

    async def feed() -> None:
        for i in range(20):
            await server.push(session, f'chunk{i:02d}'.encode())
            await asyncio.sleep(0.01)
        await server.close_session(session)

    feeder = asyncio.create_task(feed())
    async with ClientSession() as client:
        async with client.get(url) as response:
            assert response.status == 200
            # Read a little then drop — mimics probe hang-up.
            await response.content.read(8)
    await feeder


@pytest.mark.asyncio
async def test_replay_while_still_streaming(server: AudioHttpServer) -> None:
    session = server.create_session()
    url = server.url_for(session.stream_id)

    async def feed() -> None:
        await server.push(session, b'head')
        await asyncio.sleep(0.05)
        await server.push(session, b'tail')
        await server.close_session(session)

    feeder = asyncio.create_task(feed())
    async with ClientSession() as client:
        async with client.get(url) as probe:
            # Probe may finish before tail arrives; wait briefly then read all.
            body1 = await probe.read()
        async with client.get(url) as play:
            body2 = await play.read()
    await feeder
    assert body1 in (b'head', b'headtail')
    assert body2 == b'headtail'
