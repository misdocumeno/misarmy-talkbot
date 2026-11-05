"""Prometheus scrape endpoint (separate from TTS audio HTTP)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from aiohttp import web

from misarmy_talkbot.observability.logger import logger

if TYPE_CHECKING:
    from aiohttp.web import AppRunner, TCPSite

_runner: AppRunner | None = None
_site: TCPSite | None = None


async def start_metrics_http() -> None:
    """Serve ``GET /metrics`` for Prometheus when METRICS_HTTP_PORT is set."""
    global _runner, _site
    if _runner is not None:
        return
    port = int(os.getenv('METRICS_HTTP_PORT', '9100') or '0')
    if port <= 0:
        logger.info('metrics_http_disabled')
        return
    host = os.getenv('METRICS_HTTP_HOST', '0.0.0.0')

    async def metrics_handler(_request: web.Request) -> web.Response:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        body = generate_latest()
        return web.Response(
            body=body, headers={'Content-Type': CONTENT_TYPE_LATEST}
        )

    app = web.Application()
    app.router.add_get('/metrics', metrics_handler)
    _runner = web.AppRunner(app)
    await _runner.setup()
    _site = web.TCPSite(_runner, host=host, port=port)
    await _site.start()
    logger.info('metrics_http_listen host=%s port=%s', host, port)


async def stop_metrics_http() -> None:
    global _runner, _site
    if _site is not None:
        await _site.stop()
        _site = None
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
