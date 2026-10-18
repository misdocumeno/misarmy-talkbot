"""In-memory MP3 HTTP server so Lavalink can pull TTS without disk I/O.

Lavalink's ``http`` source fetches ``http://misarmy_talkbot:<port>/tts/<id>``.

Important: lavaplayer opens the URL **twice** — once to probe the container
during ``loadItem``, then again from byte 0 when the track plays
(``HttpAudioTrack.process``). A consume-once queue breaks the second GET
(empty body → socket timeout → 404). Sessions therefore keep an append-only
chunk buffer and every GET replays from the start, live-tailing while the
producer is still pushing.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field

from aiohttp import web
from aiohttp.client_exceptions import ClientConnectionResetError

from misarmy_talkbot.observability.logger import logger


@dataclass
class AudioStreamSession:
    """One TTS item exposed at ``/tts/{stream_id}``."""

    stream_id: str
    # octet-stream: Lavalink probes magic bytes (MP3 from Azure/edge, OGG from Chirp).
    content_type: str = 'application/octet-stream'
    chunks: list[bytes] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    first_byte: asyncio.Event = field(default_factory=asyncio.Event)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    bytes_pushed: int = 0
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.first_byte.is_set()


class AudioHttpServer:
    """Process-wide aiohttp app Lavalink reaches over the Compose network."""

    _instance: AudioHttpServer | None = None

    def __init__(
        self,
        *,
        host: str | None = None,
        port: int | None = None,
        public_base: str | None = None,
    ) -> None:
        self._host = host or os.getenv('AUDIO_HTTP_HOST', '0.0.0.0')
        self._port = port or int(os.getenv('AUDIO_HTTP_PORT', '8765'))
        self._public_base = (
            public_base
            or os.getenv(
                'AUDIO_HTTP_PUBLIC_BASE',
                f'http://misarmy_talkbot:{self._port}',
            )
        ).rstrip('/')
        self._sessions: dict[str, AudioStreamSession] = {}
        self._app = web.Application()
        self._app.router.add_get('/tts/{stream_id}', self._handle_get)
        self._app.router.add_get('/healthz', self._handle_health)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @classmethod
    def instance(cls) -> AudioHttpServer:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def url_for(self, stream_id: str) -> str:
        return f'{self._public_base}/tts/{stream_id}'

    def create_session(self) -> AudioStreamSession:
        stream_id = uuid.uuid4().hex
        session = AudioStreamSession(stream_id=stream_id)
        self._sessions[stream_id] = session
        return session

    async def push(self, session: AudioStreamSession, data: bytes) -> None:
        if not data:
            return
        async with session.condition:
            session.chunks.append(data)
            session.bytes_pushed += len(data)
            session.condition.notify_all()
        if not session.first_byte.is_set():
            session.first_byte.set()

    async def close_session(
        self, session: AudioStreamSession, *, error: str | None = None
    ) -> None:
        if error:
            session.error = error
        async with session.condition:
            session.closed.set()
            session.condition.notify_all()
        if not session.first_byte.is_set():
            # Empty/failed stream: unblock waiters so load can fail cleanly.
            session.first_byte.set()

    def discard(self, stream_id: str) -> None:
        self._sessions.pop(stream_id, None)

    async def publish_complete(self, payload: bytes) -> AudioStreamSession:
        """Serve a finished MP3 from RAM (non-streaming providers)."""
        session = self.create_session()
        await self.push(session, payload)
        await self.close_session(session)
        return session

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        logger.info(
            'audio_http_listen host=%s port=%s public_base=%s',
            self._host,
            self._port,
            self._public_base,
        )

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def _handle_health(self, _request: web.Request) -> web.Response:
        return web.Response(text='ok')

    async def _handle_get(self, request: web.Request) -> web.StreamResponse:
        stream_id = request.match_info['stream_id']
        session = self._sessions.get(stream_id)
        if session is None:
            raise web.HTTPNotFound(text='unknown stream')

        try:
            await asyncio.wait_for(session.first_byte.wait(), timeout=60.0)
        except TimeoutError as exc:
            raise web.HTTPGatewayTimeout(text='tts timeout') from exc

        if session.error and session.bytes_pushed == 0:
            raise web.HTTPBadGateway(text=session.error)

        headers = {
            'Content-Type': session.content_type,
            'Cache-Control': 'no-store',
            'Connection': 'close',
        }
        # When synthesis already finished, Content-Length lets lavaplayer skip
        # chunked-edge cases on the playback GET.
        if session.closed.is_set() and session.bytes_pushed > 0:
            headers['Content-Length'] = str(session.bytes_pushed)

        response = web.StreamResponse(status=200, headers=headers)
        await response.prepare(request)

        index = 0
        try:
            while True:
                async with session.condition:
                    while (
                        index >= len(session.chunks)
                        and not session.closed.is_set()
                    ):
                        await session.condition.wait()
                    if index >= len(session.chunks):
                        break
                    batch = session.chunks[index:]
                    index = len(session.chunks)
                for piece in batch:
                    await response.write(piece)

            await response.write_eof()
        except (
            ClientConnectionResetError,
            ConnectionResetError,
            BrokenPipeError,
            ConnectionError,
        ) as exc:
            # lavaplayer probe GET often hangs up after magic bytes.
            logger.debug(
                'audio_http_client_gone stream=%s err=%s',
                stream_id,
                exc,
            )
        except RuntimeError as exc:
            # asyncio: "Cannot write to closing transport"
            if 'closing transport' not in str(exc).lower():
                raise
            logger.debug(
                'audio_http_client_gone stream=%s err=%s',
                stream_id,
                exc,
            )
        return response
