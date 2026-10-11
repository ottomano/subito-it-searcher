#!/usr/bin/env python3
"""Entry point: parse args, build config, dispatch.

Nothing else lives here. All logic is in the subito/ package.
Run with --help to see all options.
"""

import logging
import logging.handlers
import os

from subito import queries as q
from subito import scraper, storage
from subito.bot import run_bot
from subito.cli import build_parser
from subito.config import DATA_DIR, LEGACY_SEARCHES_FILE, AppConfig

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else default


def _pick(flag: int | None, env: str, default: int | None) -> int | None:
    """Priority: CLI flag > env var > default."""
    return flag if flag is not None else _env_int(env, default)


def _user(args, create: bool = True) -> dict | None:
    """User selected by --chat-id, or the default user."""
    chat_id = args.chat_id if args.chat_id is not None else storage.default_chat_id()
    return storage.user_for_chat(chat_id, create)


def main() -> None:
    args = build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(DATA_DIR, "errors.log"),
        maxBytes=1_000_000,
        backupCount=3,
    )
    file_handler.setLevel(logging.WARNING)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logging.getLogger().addHandler(file_handler)
    # httpx logs every request URL at INFO, and Telegram URLs embed the bot token
    logging.getLogger("httpx").setLevel(logging.WARNING)

    storage.init_db()

    cfg = AppConfig(
        tgoff=args.tgoff,
        ntfyoff=args.ntfyoff,
        winoff=args.winoff,
        no_bot=args.no_bot,
        delay=_pick(args.delay, "DELAY", 120),
        active_hour=_pick(args.active_hour, "ACTIVE_HOUR", 0),
        pause_hour=_pick(args.pause_hour, "PAUSE_HOUR", 0),
        query_delay=_pick(args.query_delay, "QUERY_DELAY", 60),
        bot_detection_sleep=_pick(
            args.bot_detection_sleep, "BOT_DETECTION_SLEEP", 86400
        ),
        restrict_to_chat_id=_env_int("RESTRICT_BOT_TO_CHATID", None),
    )

    # Subcommands

    if args.command == "add":
        search = storage.add_search(
            _user(args)["id"],
            args.name,
            args.url,
            args.min_price,
            args.max_price,
            tuttosubito_only=args.tuttosubito_only,
        )
        # First fetch stores the current listings without notifying them
        scraper.run_searches([search], cfg)
        logger.info(f"Search '{args.name}' added.")
        return

    if args.command == "delete":
        user = _user(args, create=False)
        if user and storage.delete_search(user["id"], args.name):
            logger.info(f"Search '{args.name}' deleted.")
        else:
            logger.error(f"No search named '{args.name}'.")
        return

    if args.command == "list":
        if args.all:
            searches = storage.list_searches()
        else:
            user = _user(args, create=False)
            searches = storage.list_searches(user["id"]) if user else []
        if args.short:
            q.print_list_short(searches)
        else:
            q.print_list(searches)
        return

    if args.command == "migrate":
        for r in storage.migrate_files():
            logger.info(f"Renamed: {r}")
        confirm = input(
            "Import searches.json, telegram.json and ntfy.json into the database? "
            "Searches with the same name are replaced. [Y/n] "
        )
        if confirm.strip() != "Y":
            logger.info("Migration cancelled.")
            return
        count = storage.import_legacy_json(args.chat_id)
        owner = storage.default_chat_id() if args.chat_id is None else args.chat_id
        logger.info(
            f"Imported {count} search(es) for {owner or 'the local user'}. "
            "The JSON files can now be removed."
        )
        return

    if args.command == "setup":
        if args.token:
            storage.set_setting("telegram_token", args.token)
            logger.info("Telegram token saved.")
        if args.telegram_chat_id:
            storage.set_setting("telegram_chat_id", args.telegram_chat_id)
            logger.info("Telegram chat id saved.")
        if args.ntfy_server and args.ntfy_topic:
            storage.set_setting("ntfy_server", args.ntfy_server)
            storage.set_setting("ntfy_topic", args.ntfy_topic)
            logger.info("ntfy config saved.")
        return

    # Run modes

    if not storage.list_searches() and os.path.isfile(LEGACY_SEARCHES_FILE):
        logger.warning(
            "searches.json found but the database is empty: run `migrate` to import it."
        )

    token = storage.get_telegram_token()
    core = scraper.Scraper(cfg, token)
    if core.channels:
        logger.info(f"Output channels: {', '.join(c.name for c in core.channels)}")
    else:
        logger.warning("No output channel active: new items will only be logged.")

    try:
        if args.refresh:
            core.run(storage.list_searches())
        elif token and not cfg.no_bot:
            run_bot(core, token, cfg)
        else:
            core.loop()
    except KeyboardInterrupt:
        logger.info("Stopped.")


if __name__ == "__main__":
    main()
