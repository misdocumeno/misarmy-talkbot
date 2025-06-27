import argparse


DEFAULT_DEV_GUILD = 814851486621106198

parser = argparse.ArgumentParser(description='A text-to-speech discord bot.')
choices = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
parser.add_argument('--log-level', default='INFO', help=f'Logging level [{'|'.join(choices)}]', choices=choices)
parser.add_argument(
    '--dev-guild', default=DEFAULT_DEV_GUILD, help='Guild with command for syncing global slash commands.')
parser.add_argument('--invite-guild', default=DEFAULT_DEV_GUILD, help='Guild id for the invite command.')
parser.add_argument('--locale', help='Locale to use.')
args = parser.parse_args()
