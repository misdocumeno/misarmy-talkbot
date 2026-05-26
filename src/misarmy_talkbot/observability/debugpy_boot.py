"""Optional debugpy listen port — attach anytime; never blocks the bot."""

from __future__ import annotations

import os

from misarmy_talkbot.observability.logger import logger

_started = False


def start_debugpy_if_enabled() -> None:
    """Open a non-blocking attach port when ``ENABLE_DEBUGPY=1``.

    ``debugpy.listen`` returns immediately; the bot keeps running whether or not
    a debugger is attached. ``DEBUGPY_WAIT_FOR_CLIENT`` is ignored (would stall
    the gateway and slash-command acks).
    """
    global _started
    if _started or os.getenv('ENABLE_DEBUGPY') != '1':
        return
    if os.getenv('DEBUGPY_WAIT_FOR_CLIENT', '').lower() in (
        '1',
        'true',
        'yes',
    ):
        logger.warning(
            'DEBUGPY_WAIT_FOR_CLIENT is set but ignored; debugpy never blocks this process'
        )
    import debugpy

    port = int(os.getenv('DEBUGPY_PORT', '5678'))
    if debugpy.is_client_connected():
        _started = True
        logger.info('debugpy already attached on port %s', port)
        return
    try:
        debugpy.listen(('0.0.0.0', port))
    except RuntimeError as exc:
        if 'already' not in str(exc).lower():
            raise
        logger.info('debugpy already listening on port %s', port)
    else:
        logger.info('debugpy listening on port %s (optional attach)', port)
    _started = True
