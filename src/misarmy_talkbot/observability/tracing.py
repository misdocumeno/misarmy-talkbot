"""OpenTelemetry tracing for the speak pipeline (optional until exporter is configured)."""

from __future__ import annotations

import os
from collections.abc import Generator  # noqa: TC003
from contextlib import contextmanager

_tracer = None
_setup_done = False


def setup_tracing() -> None:
    """Configure OTLP exporter when OTEL_EXPORTER_OTLP_ENDPOINT (or TEMPO) is set."""
    global _tracer, _setup_done
    if _setup_done:
        return
    _setup_done = True
    endpoint = (
        os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
        or os.getenv('OTEL_EXPORTER_OTLP_TRACES_ENDPOINT')
        or ''
    ).strip()
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({
            'service.name': os.getenv('OTEL_SERVICE_NAME', 'misarmy-talkbot')
        })
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer('misarmy_talkbot.playback')
    except Exception:
        from misarmy_talkbot.observability.logger import logger

        logger.exception('otel_setup_failed; continuing without traces')
        _tracer = None


@contextmanager
def speak_span(
    name: str,
    *,
    guild_id: int | None = None,
    message_id: int | None = None,
    **attrs: str | int | float | bool,
) -> Generator[None]:
    """Create a child span when tracing is enabled; otherwise a no-op."""
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as span:
        if guild_id is not None:
            span.set_attribute('guild_id', guild_id)
        if message_id is not None:
            span.set_attribute('message_id', message_id)
        for key, value in attrs.items():
            span.set_attribute(key, value)
        yield
