import sys
import logging
import colorlog
import discord
from .args import args

GRAY = '\033[1;30m'
VIOLET = '\033[35m'
RESET = '\033[0m'

main = sys.modules['__main__'].__package__ or 'misarmy_talkbot'
_pkg_name = 'misarmy_talkbot'

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    f'{GRAY}%(asctime)s %(log_color)s%(levelname)-8s {RESET}' +
    f'{VIOLET}{main}.%(module)s {RESET}%(message)s', '%Y-%m-%d %H:%M:%S',
    log_colors={'DEBUG': 'white', 'INFO': 'blue', 'WARNING': 'yellow', 'ERROR': 'red', 'CRITICAL': 'bold_red'}))

_level = getattr(logging, args.log_level.upper())
logger = colorlog.getLogger(_pkg_name)
logger.handlers.clear()
logger.addHandler(handler)
logger.setLevel(_level)
logger.propagate = False

# Library noise: gateway debug is extremely verbose; raise only if diagnosing discord.py internals.
discord.player._log.setLevel(logging.WARNING)
logging.getLogger('discord.gateway').setLevel(logging.WARNING)
logging.getLogger('discord.voice_client').setLevel(logging.INFO)
