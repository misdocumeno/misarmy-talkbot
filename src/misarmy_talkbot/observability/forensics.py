"""Backward-compatible re-exports; prefer misarmy_talkbot.observability.trace."""

from misarmy_talkbot.observability.trace import (
    FORENSICS_ENABLED,
    forensic,
    trace,
)

__all__ = ['FORENSICS_ENABLED', 'dbg', 'forensic', 'trace']


def dbg(msg: str, *args: object) -> None:
    if FORENSICS_ENABLED:
        from misarmy_talkbot.observability.logger import logger

        logger.debug(msg, *args)
