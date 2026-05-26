"""Repository paths (package lives under ``src/misarmy_talkbot``; data dirs stay at repo root)."""

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent.parent
CONFIG_DIR = REPO_ROOT / 'config'
