"""Process-wide logging to stderr (colored for humans, JSON for Loki)."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

import colorlog
import discord

_setup_done = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line for Alloy/Loki ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            'ts': datetime.now(UTC).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'module': record.module,
            'msg': record.getMessage(),
        }
        if record.exc_info:
            payload['exc_info'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> logging.Logger:
    """
    Logs to stderr. ``LOG_FORMAT=json`` emits JSON lines (Loki-friendly);
    default is colored text for local/dev.
    """
    global _setup_done  # noqa: PLW0603 — module bootstrap
    if _setup_done:
        return logging.getLogger('misarmy_talkbot')

    try:
        from misarmy_talkbot import args as _args_for_log

        fallback = _args_for_log.args.log_level
    except Exception:
        fallback = 'INFO'

    pkg = sys.modules['__main__'].__package__ or 'misarmy_talkbot'
    gray = '\033[1;30m'
    violet = '\033[35m'
    reset = '\033[0m'

    lg = logging.getLogger('misarmy_talkbot')
    lg.handlers.clear()
    lg.setLevel(logging.DEBUG)

    stderr = logging.StreamHandler()
    tl = os.getenv('LOG_LEVEL', fallback).upper()
    stderr.setLevel(getattr(logging, tl, logging.INFO))
    log_format = os.getenv('LOG_FORMAT', 'text').strip().lower()
    if log_format == 'json':
        stderr.setFormatter(JsonFormatter())
    else:
        stderr.setFormatter(
            colorlog.ColoredFormatter(
                f'{gray}%(asctime)s %(log_color)s%(levelname)-8s {reset}'
                + f'{violet}{pkg}.%(module)s {reset}%(message)s',
                '%Y-%m-%d %H:%M:%S',
                log_colors={
                    'DEBUG': 'white',
                    'INFO': 'blue',
                    'WARNING': 'yellow',
                    'ERROR': 'red',
                    'CRITICAL': 'bold_red',
                },
            )
        )
    lg.addHandler(stderr)

    discord.player._log.setLevel(logging.WARNING)
    _setup_done = True
    return lg


logger = setup_logging()
