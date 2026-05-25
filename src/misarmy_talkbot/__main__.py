"""Package entry: load environment, then start logging and the Discord bot."""

import os
import sys

from dotenv import load_dotenv

from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.paths import REPO_ROOT

load_dotenv(REPO_ROOT / '.env')


def main() -> None:
    from misarmy_talkbot.app.bot import run_bot

    run_bot()


if __name__ == '__main__':
    if 'DISCORD_TOKEN' not in os.environ:
        logger.critical('DISCORD_TOKEN is not set')
        sys.exit(1)
    main()
