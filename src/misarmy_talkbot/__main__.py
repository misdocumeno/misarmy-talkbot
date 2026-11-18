"""Package entry: load environment, then start logging and the Discord bot."""

import os
import sys

from dotenv import load_dotenv

from misarmy_talkbot.observability.logger import logger
from misarmy_talkbot.observability.startup import startup_tracker
from misarmy_talkbot.paths import REPO_ROOT

load_dotenv(REPO_ROOT / '.env')
startup_tracker().checkpoint('config')


def main() -> None:
    from misarmy_talkbot.app.bot import run_bot

    # Only discord.py and the bot shell are loaded here; the rest of the app is
    # imported during the Discord login (see ``app.bot``).
    startup_tracker().checkpoint('imports_discord')
    run_bot()


if __name__ == '__main__':
    if 'DISCORD_TOKEN' not in os.environ:
        logger.critical('DISCORD_TOKEN is not set')
        sys.exit(1)
    main()
