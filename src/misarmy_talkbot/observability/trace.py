"""Structured control-flow tracing — debugger-style steps without a debugger.

Every ``step()`` line is one point in the call graph. Compare sequences between a
heard message and a silent one; the first missing EXIT or extra STUCK pinpoints the block.

Phases: ENTER, EXIT, POINT, LOOP, LOOP_END, SKIP, RETRY, STUCK
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

FORENSICS_ENABLED = os.getenv('FORENSICS_ENABLED', '').lower() in (
    '1',
    'true',
    'yes',
)

_seq = 0


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


def _task_name() -> str | None:
    task = asyncio.current_task()
    return task.get_name() if task else None


def _format_fields(**kwargs: Any) -> str:
    return ' '.join(f'{key}={value!r}' for key, value in kwargs.items())


def step(
    guild_id: int | None,
    component: str,
    fn: str,
    phase: str,
    **fields: Any,
) -> int:
    """Emit one control-flow step. Returns monotonic ``seq`` for correlation."""
    from misarmy_talkbot.observability.logger import logger

    seq = _next_seq()
    base: dict[str, Any] = {
        'seq': seq,
        'ts': round(time.time(), 3),
        'task': _task_name(),
    }
    if guild_id is not None:
        base['guild_id'] = guild_id
    base.update(fields)
    body = _format_fields(**base)
    logger.debug('TRACE %s.%s %s %s', component, fn, phase, body)
    return seq


def trace(component: str, event: str, **fields: Any) -> None:
    """Backward-compatible alias → ``step(..., POINT)``."""
    guild_id = fields.pop('guild_id', None)
    step(guild_id, component, event, 'POINT', **fields)


def forensic(component: str, event: str, **fields: Any) -> None:
    if not FORENSICS_ENABLED:
        return
    from misarmy_talkbot.observability.logger import logger

    guild_id = fields.pop('guild_id', None)
    base: dict[str, Any] = {'task': _task_name()}
    if guild_id is not None:
        base['guild_id'] = guild_id
    base.update(fields)
    logger.debug('FORENSIC %s.%s %s', component, event, _format_fields(**base))
