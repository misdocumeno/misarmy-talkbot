import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import colorlog
import discord


def _parse_max_bytes(raw: str) -> int:
    raw = raw.strip().upper().replace('_', '').replace(' ', '')
    if raw.endswith('MIB'):
        return int(raw[:-3]) * 1024 * 1024
    if raw.endswith('MB'):
        return int(raw[:-2]) * 1000 * 1000
    return int(raw)


_setup_done = False


def setup_logging() -> logging.Logger:
    """
    Stderr/colored INFO (override via LOG_LEVEL / CLI fallback) + rotating file at DEBUG when LOG_FILE is set.
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

    path = os.getenv('LOG_FILE', '').strip()
    if path:
        try:
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            fh = RotatingFileHandler(
                path,
                maxBytes=_parse_max_bytes(
                    os.getenv('LOG_FILE_MAX_BYTES', '52428800')
                ),
                backupCount=int(os.getenv('LOG_FILE_BACKUP_COUNT', '5')),
                encoding='utf-8',
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(
                logging.Formatter(
                    '%(asctime)s %(levelname)-8s %(name)s.%(module)s %(message)s',
                    '%Y-%m-%d %H:%M:%S',
                )
            )
            lg.addHandler(fh)
        except OSError as exc:
            lg.warning(
                'LOG_FILE disabled (%s): %s — using stderr only',
                path,
                exc,
            )

    discord.player._log.setLevel(logging.WARNING)
    _setup_done = True
    return lg


logger = setup_logging()
