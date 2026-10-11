"""Paths and shared configuration.

Defines DATA_DIR (relative to project root, Docker-safe), the SQLite database path and
the legacy JSON paths read only by `migrate`. AppConfig carries every run parameter so
no module needs to import argparse.Namespace directly.
"""

from dataclasses import dataclass
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "subito.db")

# Legacy JSON state, imported into SQLite by `migrate`
LEGACY_SEARCHES_FILE = os.path.join(DATA_DIR, "searches.json")
LEGACY_TG_CREDS_FILE = os.path.join(DATA_DIR, "telegram.json")
LEGACY_NTFY_FILE = os.path.join(DATA_DIR, "ntfy.json")


@dataclass
class AppConfig:
    tgoff: bool = False
    ntfyoff: bool = False
    winoff: bool = False
    no_bot: bool = False
    delay: int = 120
    active_hour: int = 0
    pause_hour: int = 0
    query_delay: int = 60
    bot_detection_sleep: int = 86400
    restrict_to_chat_id: int | None = None
