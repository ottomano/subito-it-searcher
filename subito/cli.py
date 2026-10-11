"""Command-line interface: subcommands for actions, flags for run control.

Subcommands: add, delete, list, migrate, setup
Run modes (top-level flags): default polling loop (+ Telegram bot if a token is set,
unless --no-bot), --refresh. Output channels are switched off with --<name>off.

Subcommands that act on searches take --chat-id; without it they act on the default
user (TELEGRAM_CHAT_ID, then RESTRICT_BOT_TO_CHATID, then the local user).
"""

import argparse


def _add_chat_id(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--chat-id",
        dest="chat_id",
        help="Telegram chat id of the user (default: the default user)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Monitor subito.it and get notified when new items match your searches. "
            "Without a subcommand, starts polling (and the Telegram bot if a token "
            "is configured)."
        ),
    )

    parser.add_argument(
        "--refresh",
        "-r",
        dest="refresh",
        action="store_true",
        help="Run every user's searches once, send notifications and exit",
    )
    parser.set_defaults(refresh=False)
    parser.add_argument(
        "--no-bot",
        dest="no_bot",
        action="store_true",
        help="Do not answer Telegram commands (Telegram stays an output channel)",
    )

    parser.add_argument(
        "--delay",
        dest="delay",
        default=None,
        type=int,
        help="Polling interval in seconds (default: 120)",
    )
    parser.add_argument(
        "--query-delay",
        dest="query_delay",
        default=None,
        type=int,
        help=(
            "Seconds to wait between queries to avoid anti-bot detection (default: 60)"
        ),
    )
    parser.add_argument(
        "--bot-detection-sleep",
        dest="bot_detection_sleep",
        default=None,
        type=int,
        help="Seconds to sleep after bot detection (default: 86400)",
    )
    parser.add_argument(
        "--active-hour",
        "-ah",
        dest="active_hour",
        type=int,
        help="Hour when to start polling in 24h notation (e.g. 8 for 08:00)",
    )
    parser.add_argument(
        "--pause-hour",
        "-ph",
        dest="pause_hour",
        type=int,
        help="Hour when to stop polling in 24h notation (e.g. 23 for 23:00)",
    )

    # Output channels
    out = parser.add_argument_group("output channels")
    out.add_argument(
        "--tgoff",
        dest="tgoff",
        action="store_true",
        help="Disable Telegram notifications (the bot still answers commands)",
    )
    out.add_argument(
        "--ntfyoff", dest="ntfyoff", action="store_true", help="Disable ntfy"
    )
    out.add_argument(
        "--winoff",
        dest="winoff",
        action="store_true",
        help="Disable Windows toast notifications",
    )

    # Subcommands
    sub = parser.add_subparsers(dest="command")

    # Add
    p_add = sub.add_parser("add", help="Add a new search")
    p_add.add_argument("name", help="Name for this search")
    p_add.add_argument("url", help="Subito.it search URL")
    p_add.add_argument(
        "--min-price", dest="min_price", type=int, help="Minimum price (e.g. 50)"
    )
    p_add.add_argument(
        "--max-price", dest="max_price", type=int, help="Maximum price (e.g. 200)"
    )
    p_add.add_argument(
        "--tuttosubito",
        dest="tuttosubito_only",
        action="store_true",
        help=(
            "Only track items shipped via TuttoSubito "
            "(verified dealer + buyer protection)"
        ),
    )
    p_add.set_defaults(tuttosubito_only=False)
    _add_chat_id(p_add)

    # Delete
    p_del = sub.add_parser("delete", help="Delete a search")
    p_del.add_argument("name", help="Name of the search to delete")
    _add_chat_id(p_del)

    # List
    p_list = sub.add_parser("list", help="List searches")
    p_list.add_argument(
        "--short",
        dest="short",
        action="store_true",
        help="Compact output",
    )
    p_list.set_defaults(short=False)
    _add_chat_id(p_list)
    p_list.add_argument(
        "--all", dest="all", action="store_true", help="List every user's searches"
    )

    # Migrate
    p_mig = sub.add_parser(
        "migrate",
        help="Import searches.json, telegram.json and ntfy.json into SQLite",
    )
    p_mig.add_argument(
        "--chat-id",
        dest="chat_id",
        help="Owner of the imported searches (default: the default user, which is "
        "the chatid in telegram.json unless TELEGRAM_CHAT_ID says otherwise)",
    )

    # Setup
    p_setup = sub.add_parser("setup", help="Configure Telegram and ntfy")
    tg = p_setup.add_argument_group("telegram")
    tg.add_argument("--telegram-token", dest="token", help="Telegram bot API token")
    tg.add_argument(
        "--telegram-chat-id",
        dest="telegram_chat_id",
        help="Chat or @channel receiving the default user's notifications",
    )
    ntfy = p_setup.add_argument_group("ntfy")
    ntfy.add_argument("--ntfy-server", dest="ntfy_server", help="ntfy server URL")
    ntfy.add_argument("--ntfy-topic", dest="ntfy_topic", help="ntfy topic")

    return parser
