import logging
import os
import sys

import colorlog
import discord

_setup_done = False


def setup_logging() -> logging.Logger:
    """
    Colored logs to stderr (level from LOG_LEVEL env or CLI fallback).
    Safe to call more than once; duplicate handlers avoided.
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
