import argparse
import os


DEFAULT_DEV_GUILD = 814851486621106198

parser = argparse.ArgumentParser(description='A text-to-speech discord bot.')
choices = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
_env_log = os.environ.get('LOG_LEVEL', '').strip().upper()
_default_log_level = _env_log if _env_log in choices else 'INFO'
parser.add_argument(
    '--log-level',
    default=_default_log_level,
    help=(
        f'Logging level [{'|'.join(choices)}]. '
        'Default comes from LOG_LEVEL env if set (Docker-friendly).'
    ),
    choices=choices,
)
parser.add_argument(
    '--dev-guild', default=DEFAULT_DEV_GUILD, help='Guild with command for syncing global slash commands.')
parser.add_argument('--invite-guild', default=DEFAULT_DEV_GUILD, help='Guild id for the invite command.')
parser.add_argument('--locale', help='Locale to use.')
args = parser.parse_args()
