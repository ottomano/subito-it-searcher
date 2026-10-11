"""Persistence layer: SQLite database in data/subito.db.

Tables:
  settings   global key/value pairs: telegram_token, telegram_chat_id, ntfy_server,
             ntfy_topic
  users      one row per Telegram chat id; telegram_chat_id NULL is the local user,
             for setups without Telegram
  searches   saved searches, unique by (user_id, name)
  items      listings found per search, keyed by (search_id, link)

The default user (CLI without --chat-id, ntfy and Windows destination) is the one whose
telegram_chat_id is TELEGRAM_CHAT_ID, falling back to RESTRICT_BOT_TO_CHATID and then
to the local user.

Every function opens its own short-lived connection, so the bot can call storage from
the event loop and from the scraping thread at the same time. WAL mode lets readers
proceed while a write is in progress.

A search with last_checked NULL has never been fetched: the first fetch stores its
items silently instead of notifying them (see scraper.run_query).

Environment variables override stored settings: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
NTFY_SERVER, NTFY_TOPIC.

import_legacy_json() imports the files written by older versions (searches.json,
telegram.json, ntfy.json); migrate_queries() converts the oldest nested format first.
"""

from contextlib import closing, contextmanager
from datetime import datetime
import json
import os
import sqlite3

from subito.config import (
    DATA_DIR,
    DB_PATH,
    LEGACY_NTFY_FILE,
    LEGACY_SEARCHES_FILE,
    LEGACY_TG_CREDS_FILE,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    telegram_chat_id TEXT UNIQUE,
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    id               INTEGER PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    url              TEXT NOT NULL,
    min_price        INTEGER,
    max_price        INTEGER,
    tuttosubito_only INTEGER NOT NULL DEFAULT 0,
    last_checked     TEXT,
    UNIQUE (user_id, name)
);
CREATE TABLE IF NOT EXISTS items (
    search_id       INTEGER NOT NULL REFERENCES searches(id) ON DELETE CASCADE,
    link            TEXT NOT NULL,
    title           TEXT,
    price           INTEGER,
    location        TEXT,
    date            TEXT,
    shipping        INTEGER NOT NULL DEFAULT 0,
    tuttosubito     INTEGER NOT NULL DEFAULT 0,
    verified_dealer INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (search_id, link)
);
"""

_ITEM_FIELDS = (
    "title",
    "price",
    "location",
    "date",
    "shipping",
    "tuttosubito",
    "verified_dealer",
)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _connect():
    """Yield a connection that commits on success and rolls back on error."""
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        with conn:
            yield conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)


# Settings


def get_setting(key: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def get_telegram_token() -> str | None:
    return os.environ.get("TELEGRAM_TOKEN") or get_setting("telegram_token")


def get_ntfy() -> tuple[str, str] | None:
    """Return (server, topic) when ntfy is configured, else None."""
    server = os.environ.get("NTFY_SERVER") or get_setting("ntfy_server")
    topic = os.environ.get("NTFY_TOPIC") or get_setting("ntfy_topic")
    return (server, topic) if server and topic else None


# Users


def get_user(user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def user_for_chat(chat_id: str | int | None, create: bool = True) -> dict | None:
    """Return the user for a Telegram chat id (None: the local user).

    A missing user is created, or None is returned when create is False.
    """
    chat_id = None if chat_id is None else str(chat_id)
    with _connect() as conn:
        if chat_id is None:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_chat_id IS NULL"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_chat_id = ?", (chat_id,)
            ).fetchone()
        if row:
            return dict(row)
        if not create:
            return None
        cur = conn.execute(
            "INSERT INTO users (telegram_chat_id, created_at) VALUES (?, ?)",
            (chat_id, _now()),
        )
        return {"id": cur.lastrowid, "telegram_chat_id": chat_id, "created_at": _now()}


def default_chat_id() -> str | None:
    return (
        os.environ.get("TELEGRAM_CHAT_ID")
        or get_setting("telegram_chat_id")
        or os.environ.get("RESTRICT_BOT_TO_CHATID")
    )


def default_user() -> dict:
    return user_for_chat(default_chat_id())


# Searches


def list_searches(user_id: int | None = None) -> list[dict]:
    """Return the searches of one user, or of every user when user_id is None."""
    with _connect() as conn:
        if user_id is None:
            rows = conn.execute("SELECT * FROM searches ORDER BY user_id, id")
        else:
            rows = conn.execute(
                "SELECT * FROM searches WHERE user_id = ? ORDER BY id", (user_id,)
            )
        return [dict(r) for r in rows]


def get_search(user_id: int, name: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM searches WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()
    return dict(row) if row else None


def add_search(
    user_id: int,
    name: str,
    url: str,
    min_price: int | None,
    max_price: int | None,
    tuttosubito_only: bool = False,
) -> dict:
    """Create a search, replacing one with the same name and resetting its items."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM searches WHERE user_id = ? AND name = ?", (user_id, name)
        )
        conn.execute(
            "INSERT INTO searches "
            "(user_id, name, url, min_price, max_price, tuttosubito_only) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, name, url, min_price, max_price, int(tuttosubito_only)),
        )
    return get_search(user_id, name)


def delete_search(user_id: int, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM searches WHERE user_id = ? AND name = ?", (user_id, name)
        )
    return cur.rowcount > 0


# Items


def item_links(search_id: int) -> set[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT link FROM items WHERE search_id = ?", (search_id,))
        return {r["link"] for r in rows}


def list_items(search_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM items WHERE search_id = ? ORDER BY rowid", (search_id,)
        )
        return [dict(r) for r in rows]


def save_query_result(
    search_id: int, new_items: dict[str, dict], sold_links: set[str]
) -> None:
    """Store new items, drop sold ones and mark the search as checked, atomically."""
    with _connect() as conn:
        _insert_items(conn, search_id, new_items)
        conn.executemany(
            "DELETE FROM items WHERE search_id = ? AND link = ?",
            [(search_id, link) for link in sold_links],
        )
        conn.execute(
            "UPDATE searches SET last_checked = ? WHERE id = ?", (_now(), search_id)
        )


def _insert_items(conn: sqlite3.Connection, search_id: int, items: dict) -> None:
    rows = []
    for link, item in items.items():
        price = item.get("price")
        rows.append(
            (
                search_id,
                link,
                item.get("title"),
                price if isinstance(price, int) else None,
                item.get("location"),
                item.get("date"),
                int(bool(item.get("shipping"))),
                int(bool(item.get("tuttosubito"))),
                int(bool(item.get("verified_dealer"))),
            )
        )
    conn.executemany(
        f"INSERT OR IGNORE INTO items (search_id, link, {', '.join(_ITEM_FIELDS)}) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# Legacy JSON import


def migrate_files() -> list[str]:
    """Rename data files from the oldest naming convention to the .json names.

    Old names: searches.tracked, telegram_api_credentials, ntfy_config
    New names: searches.json, telegram.json, ntfy.json

    Returns a list of rename descriptions for logging.
    Skips a rename if the destination already exists.
    """
    renames = [
        ("searches.tracked", "searches.json"),
        ("telegram_api_credentials", "telegram.json"),
        ("ntfy_config", "ntfy.json"),
    ]
    done = []
    for old_name, new_name in renames:
        old_path = os.path.join(DATA_DIR, old_name)
        new_path = os.path.join(DATA_DIR, new_name)
        if os.path.isfile(old_path) and not os.path.isfile(new_path):
            os.rename(old_path, new_path)
            done.append(f"{old_name} -> {new_name}")
    return done


def migrate_queries(old: dict) -> dict:
    """Convert the old nested structure to the flat structure.

    Old: queries[name][url][min_price][max_price][link] = {title, price, location}
    New: queries[name] = {url, min_price (int|None), max_price (int|None),
                          items: {link: {title, price, location}}}
    """
    new = {}
    for name, urls in old.items():
        for url, min_prices in urls.items():
            for min_p, max_prices in min_prices.items():
                for max_p, items in max_prices.items():
                    new[name] = {
                        "url": url,
                        "min_price": None if min_p == "null" else int(min_p),
                        "max_price": None if max_p == "null" else int(max_p),
                        "items": items,
                    }
    return new


def _load_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


def import_legacy_json(chat_id: str | None = None) -> int:
    """Import searches.json, telegram.json and ntfy.json.

    Token, chat id and ntfy go to settings. Searches go to the user of chat_id, or to
    the default user. Searches already present with the same name are replaced.
    Imported searches keep their items and are marked as checked, so nothing is
    notified again. Returns the number of imported searches.
    """
    queries = _load_json(LEGACY_SEARCHES_FILE)
    if queries and any("url" not in s for s in queries.values()):
        queries = migrate_queries(queries)

    tg = _load_json(LEGACY_TG_CREDS_FILE)
    if tg.get("token"):
        set_setting("telegram_token", tg["token"])
    if tg.get("chatid"):
        set_setting("telegram_chat_id", str(tg["chatid"]))

    ntfy = _load_json(LEGACY_NTFY_FILE)
    if ntfy.get("ntfy_server") and ntfy.get("ntfy_topic"):
        set_setting("ntfy_server", ntfy["ntfy_server"])
        set_setting("ntfy_topic", ntfy["ntfy_topic"])

    user = user_for_chat(chat_id) if chat_id is not None else default_user()
    with _connect() as conn:
        for name, search in queries.items():
            conn.execute(
                "DELETE FROM searches WHERE user_id = ? AND name = ?",
                (user["id"], name),
            )
            cur = conn.execute(
                "INSERT INTO searches (user_id, name, url, min_price, max_price, "
                "tuttosubito_only, last_checked) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    user["id"],
                    name,
                    search["url"],
                    search.get("min_price"),
                    search.get("max_price"),
                    int(bool(search.get("tuttosubito_only"))),
                    _now(),
                ),
            )
            _insert_items(conn, cur.lastrowid, search.get("items", {}))
    return len(queries)
