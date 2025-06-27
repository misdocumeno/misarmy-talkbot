import sys
import logging
import colorlog
import discord
from .args import args

GRAY = '\033[1;30m'
VIOLET = '\033[35m'
RESET = '\033[0m'

main = sys.modules['__main__'].__package__

handler = colorlog.StreamHandler()
handler.setFormatter(colorlog.ColoredFormatter(
    f'{GRAY}%(asctime)s %(log_color)s%(levelname)-8s {RESET}' +
    f'{VIOLET}{main}.%(module)s {RESET}%(message)s', '%Y-%m-%d %H:%M:%S',
    log_colors={'DEBUG': 'white', 'INFO': 'blue', 'WARNING': 'yellow', 'ERROR': 'red', 'CRITICAL': 'bold_red'}))
logger = colorlog.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel(getattr(logging, args.log_level.upper()))
discord.player._log.setLevel(logging.WARNING)
