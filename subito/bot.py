"""Telegram bot: manage searches from a chat.

The bot only handles commands (/list /add /delete /refresh /status /help). Scraping and
delivery belong to scraper.Scraper: its polling loop runs in a daemon thread started
after the bot initialises, and new items reach Telegram through the telegram output
channel like in every other mode.

Users:
  RESTRICT_BOT_TO_CHATID set    only that chat is accepted (other updates are logged
                                and dropped) and it acts on the default user, whose
                                notifications go to TELEGRAM_CHAT_ID or, if unset,
                                back to the same chat
  RESTRICT_BOT_TO_CHATID unset  every chat is its own user and receives its
                                notifications in the same chat

/refresh and the initial fetch of /add run Scraper.run in a worker thread
(asyncio.to_thread) without waiting for the lock, so a busy or paused scraper is
reported instead of stalling the command.
"""

import asyncio
import logging
import threading

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from subito import queries as q
from subito import storage
from subito.config import AppConfig
from subito.scraper import Scraper

logger = logging.getLogger(__name__)


def _user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> dict:
    if context.application.bot_data["cfg"].restrict_to_chat_id is not None:
        return storage.default_user()
    return storage.user_for_chat(update.effective_chat.id)


def _busy_text(scraper: Scraper) -> str:
    if until := scraper.paused():
        return f"Paused after bot detection until {until:%Y-%m-%d %H:%M}."
    return "A search cycle is running, try again when it ends."


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 *Subito Searcher Bot*\n\n"
        "Available commands:\n"
        "/list - show your searches\n"
        "/add `<name words> <url> [min] [max] [tuttosubito]` - add a search\n"
        "/delete `<name>` - remove a search\n"
        "/refresh - run your searches now\n"
        "/status - show daemon state\n"
        "/help - this message"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    searches = storage.list_searches(_user(update, context)["id"])
    text = q.format_searches(searches, bold=True) or "No active searches."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    usage = "Usage: /add `<name> <url> [min_price] [max_price] [tuttosubito]`"

    url_idx = next((i for i, a in enumerate(args) if a.startswith("http")), None)
    if url_idx is None or url_idx == 0:
        await update.message.reply_text(usage, parse_mode=ParseMode.MARKDOWN)
        return

    name = " ".join(args[:url_idx])
    url = args[url_idx]
    extra = args[url_idx + 1 :]
    tuttosubito_only = "tuttosubito" in extra
    prices = [a for a in extra if a != "tuttosubito"]
    try:
        min_price = q.to_price(prices[0]) if len(prices) > 0 else None
        max_price = q.to_price(prices[1]) if len(prices) > 1 else None
    except ValueError:
        await update.message.reply_text(usage, parse_mode=ParseMode.MARKDOWN)
        return

    user = _user(update, context)
    search = storage.add_search(
        user["id"], name, url, min_price, max_price, tuttosubito_only
    )

    # Store the current listings now, so the next cycle only reports what is new.
    # If the scraper is busy or paused, its next cycle does it silently instead.
    scraper = context.application.bot_data["scraper"]
    result = await asyncio.to_thread(scraper.run, [search], False)
    if result is None:
        text = f"Search *{name}* added, it will be checked in the next cycle."
    elif result[1]:
        text = f"Search *{name}* added, but bot detection triggered."
    else:
        count = len(storage.item_links(search["id"]))
        text = f"Search *{name}* added, {count} current item(s) stored."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /delete `<name>`", parse_mode=ParseMode.MARKDOWN
        )
        return

    name = " ".join(args)
    if storage.delete_search(_user(update, context)["id"], name):
        text = f"Search *{name}* deleted."
    else:
        text = f"No search named *{name}* found."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scraper = context.application.bot_data["scraper"]
    if scraper.paused() or scraper.lock.locked():
        await update.message.reply_text(_busy_text(scraper))
        return

    searches = storage.list_searches(_user(update, context)["id"])
    if not searches:
        await update.message.reply_text("No active searches.")
        return

    minutes = (len(searches) - 1) * scraper.cfg.query_delay // 60
    await update.message.reply_text(
        f"Running {len(searches)} search(es), about {minutes} min..."
    )
    result = await asyncio.to_thread(scraper.run, searches, False)
    if result is None:
        await update.message.reply_text(_busy_text(scraper))
    elif not result[1]:
        await update.message.reply_text(f"Done, {result[0]} new item(s).")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    scraper = context.application.bot_data["scraper"]
    cfg = scraper.cfg
    last = scraper.last_run
    last_str = last.strftime("%Y-%m-%d %H:%M:%S") if last else "never"
    if scraper.lock.locked():
        state = "searching now"
    elif until := scraper.paused():
        state = f"paused until {until:%Y-%m-%d %H:%M}"
    else:
        state = "active"
    channels = ", ".join(c.name for c in scraper.channels) or "none"
    searches = storage.list_searches(_user(update, context)["id"])
    text = (
        f"*Daemon status*\n"
        f"State: {state}\n"
        f"Last run: {last_str}\n"
        f"Delay: {cfg.delay}s, between queries: {cfg.query_delay}s\n"
        f"Active window: {cfg.active_hour}:00 → {cfg.pause_hour}:00\n"
        f"Output channels: {channels}\n"
        f"Your searches: {len(searches)}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def _log_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    chat_id = chat.id if chat else "unknown"
    logger.warning(f"Ignored update from unauthorized chat {chat_id}")


async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log handler and getUpdates errors; network blips get one line, no traceback."""
    if isinstance(context.error, NetworkError):
        logger.warning(f"Telegram network error: {context.error}")
    else:
        logger.error("Unhandled error in bot handler", exc_info=context.error)


async def _post_init(app: Application) -> None:
    scraper = app.bot_data["scraper"]
    threading.Thread(target=scraper.loop, name="polling", daemon=True).start()


def run_bot(scraper: Scraper, token: str, cfg: AppConfig) -> None:
    # concurrent_updates: a long /refresh must not hold back other commands
    app = (
        ApplicationBuilder()
        .token(token)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )
    app.bot_data.update({"cfg": cfg, "scraper": scraper})

    # RESTRICT_BOT_TO_CHATID locks the bot to one chat; other updates are logged and
    # dropped. Unset means anyone who finds the bot can use it.
    if cfg.restrict_to_chat_id is not None:
        allowed = filters.Chat(chat_id=cfg.restrict_to_chat_id)
        logger.info(f"Bot restricted to chat {cfg.restrict_to_chat_id}")
    else:
        allowed = filters.ALL
        logger.warning("RESTRICT_BOT_TO_CHATID not set, the bot answers any chat")
    commands = {
        "start": cmd_start,
        "help": cmd_help,
        "list": cmd_list,
        "add": cmd_add,
        "delete": cmd_delete,
        "refresh": cmd_refresh,
        "status": cmd_status,
    }
    for command, callback in commands.items():
        app.add_handler(CommandHandler(command, callback, filters=allowed))
    if cfg.restrict_to_chat_id is not None:
        app.add_handler(MessageHandler(~allowed, _log_unauthorized))
    app.add_error_handler(_error_handler)

    logger.info("Bot started. Waiting for commands...")
    app.run_polling()
