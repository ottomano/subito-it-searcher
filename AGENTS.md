# subito-it-searcher - Claude Code context

## What this project does

Scrapes subito.it (Italian classifieds) for search queries, diffs results against the
items stored in SQLite, and delivers new listings to N independent output channels
(Telegram, ntfy, Windows toast). A Telegram bot can additionally manage the searches
from a chat; without it, searches are managed from the CLI.

## Running the project

```bash
pip3 install -r requirements.txt
python3 subito-searcher.py --help
python3 subito-searcher.py <subcommand> --help
```

No build step. `data/subito.db` is created automatically on first run.

## Module map

| File | Role |
| --- | --- |
| `subito-searcher.py` | Entry point: parse args, build `AppConfig`, dispatch |
| `subito/scraper.py` | Core: fetch, parse, diff + `Scraper` runtime (loop, lock, pause, delivery). First file to check when the site breaks |
| `subito/notifications.py` | Output channels (telegram, ntfy, windows) and `build_channels()` |
| `subito/bot.py` | Telegram command interface (python-telegram-bot v22) |
| `subito/queries.py` | Search formatting, print and price parsing helpers |
| `subito/storage.py` | SQLite schema and queries + legacy JSON import |
| `subito/cli.py` | Subcommands and top-level flags |
| `subito/config.py` | `DATA_DIR`, `DB_PATH`, `AppConfig` dataclass |

## CLI structure

**Subcommands** (execute and exit):

```bash
python3 subito-searcher.py add <name> <url> [--min-price N] [--max-price N] [--tuttosubito]
                                  [--chat-id ID]
python3 subito-searcher.py delete <name> [--chat-id ID]
python3 subito-searcher.py list [--short] [--chat-id ID | --all]
python3 subito-searcher.py migrate [--chat-id ID]
python3 subito-searcher.py setup [--telegram-token T] [--telegram-chat-id ID]
                                  [--ntfy-server S --ntfy-topic T]
```

Without `--chat-id` the subcommands act on the default user (see Users below).

**Run modes**:

```bash
python3 subito-searcher.py              # polling loop + Telegram bot if a token is set
python3 subito-searcher.py --no-bot     # polling loop only, no Telegram commands
python3 subito-searcher.py --refresh    # run every user's searches once and exit
python3 subito-searcher.py --delay 30 --active-hour 8 --pause-hour 23
python3 subito-searcher.py --query-delay 60        # seconds between queries (default: 60)
python3 subito-searcher.py --bot-detection-sleep 86400  # sleep on bot detection (default: 86400)
```

**Output channel flags**: `--tgoff`, `--ntfyoff`, `--winoff`. `--tgoff` only stops
Telegram notifications; the bot still answers commands.

## Configuration priority

```text
CLI flags > env vars > database > defaults
```

Environment variables:

| Variable | Description |
| --- | --- |
| `TELEGRAM_TOKEN` | Telegram bot API token |
| `TELEGRAM_CHAT_ID` | Chat or `@channel` receiving the default user's notifications |
| `NTFY_SERVER` | ntfy server URL |
| `NTFY_TOPIC` | ntfy topic |
| `DELAY` | Polling interval in seconds (default: 120) |
| `ACTIVE_HOUR` | Hour when to start polling in 24h notation |
| `PAUSE_HOUR` | Hour when to stop polling in 24h notation |
| `QUERY_DELAY` | Seconds to wait between queries to avoid anti-bot detection (default: 60) |
| `BOT_DETECTION_SLEEP` | Seconds to sleep after bot detection (default: 86400) |
| `RESTRICT_BOT_TO_CHATID` | If set, the bot answers only this chat id; unset means open to anyone. Also the fallback for `TELEGRAM_CHAT_ID` |

## Data files

All files live in `data/` (gitignored, mounted as Docker volume):

| File | Content |
| --- | --- |
| `subito.db` | SQLite database (WAL mode, so also `subito.db-wal` / `-shm`) |
| `errors.log` | WARNING+ log records, rotating 1 MB x 3 backups |
| `searches.json`, `telegram.json`, `ntfy.json` | Legacy state, read only by `migrate` |

## Data model

| Table | Content |
| --- | --- |
| `settings` | Global key/value pairs: `telegram_token`, `telegram_chat_id`, `ntfy_server`, `ntfy_topic` |
| `users` | `id`, `telegram_chat_id` (`TEXT`, can be `@channel`; `NULL` = local user) |
| `searches` | `user_id`, `name`, `url`, `min_price`, `max_price`, `tuttosubito_only`, `last_checked`; unique by `(user_id, name)` |
| `items` | Listings per search, keyed by `(search_id, link)` |

- `min_price` / `max_price` are `INTEGER` or `NULL`. They are a local filter inherited
  from the original 2019 project: they let a user change the price range without
  editing the URL or resetting the stored items. Keep them
- `tuttosubito_only` is a boolean, filter applied during scraping
- Use `&shp=true` in the URL for shipping-only results (native subito.it filter)
- Each item stores: `title`, `price` (`NULL` when unknown), `location`, `date`,
  `shipping`, `tuttosubito`, `verified_dealer`
- `last_checked IS NULL` means never fetched: the first fetch stores items without
  notifying them, so a new search does not flood the chat
- Deleting a search cascades to its items
- Run `python3 subito-searcher.py migrate` once to import files written by older
  versions; it also converts the oldest nested JSON format

## Users

- The default user is the one whose `telegram_chat_id` is `TELEGRAM_CHAT_ID` (env >
  settings), falling back to `RESTRICT_BOT_TO_CHATID`, then to the local user
  (`telegram_chat_id NULL`, for setups without Telegram)
- CLI subcommands without `--chat-id` act on the default user; read-only ones never
  create users
- Bot with `RESTRICT_BOT_TO_CHATID`: only that chat is accepted and it acts on the
  default user
- Bot without it: every chat is its own user and receives notifications in the chat

## Tooling

```bash
uv pip install -r requirements-dev.txt   # install black and ruff
python -m black subito/ subito-searcher.py
python -m ruff check --fix subito/ subito-searcher.py
```

- **black**: code formatter, 88-char line length (default)
- **ruff**: linting and import sorting (`E`, `F`, `I` rules), configured in `ruff.toml`
- String literals in `HEADERS` (scraper.py) are exempt with `# noqa: E501`

## Key conventions

- Snake case everywhere: CLI flags use kebab-case, Python variables and JSON keys use
  snake_case
- f-strings for all string formatting including logger calls
- Doc/comment lines: maximum 88 characters
- No globals: config built in `main()` and passed explicitly; state lives in SQLite
- `AppConfig` replaces passing `argparse.Namespace` into modules
- `print()` only in `queries.py` display functions; everything else uses `logging`
- Log levels: `INFO` normal flow, `DEBUG` per-item detail, `WARNING` network errors,
  `ERROR` failures

## Scraper internals

subito.it is a Next.js app. Results are embedded as JSON in a `<script id="__NEXT_DATA__">`
tag. The extraction path is:

```text
props -> pageProps -> initialState -> items -> originalList
```

Each element is a product dict directly (no wrapper). If the site returns no results
unexpectedly, check this path first in `subito/scraper.py`.

Extracted fields: `title`, `link`, `date`, `location`, `price`,
`shipping` (`/item_shippable` key `"1"`),
`tuttosubito` (`/item_shipping_type` == `"Spedizione con TuttoSubito"`),
`verified_dealer` (`proTransactionsEnabled: True` and tuttosubito).
All extracted defensively with `.get()` inside the existing `try/except`:
a missing field returns `False`, not a crash.

The `HEADERS` dict in `scraper.py` is browser impersonation. Do not simplify it.

## Anti-bot detection (DataDome)

Subito.it uses DataDome. When it triggers, the scraper receives a 403 or an HTML page
containing `captcha-delivery` / "Please enable JS" instead of the listing HTML.
The scraper detects this and logs a warning (`Could not find JSON data on page`).

Recovery steps when the IP is flagged:

1. Stop the script immediately — continuing burns IP reputation further.
2. Open subito.it in a real browser and solve any captcha presented.
3. Wait until a fresh browser session loads search results without a redirect.
4. Restart the script with conservative settings (few searches, long delays).

What helps avoid triggering DataDome:

- Browser-like `HEADERS` in `scraper.py` — do not simplify them.
- `query_delay` between requests (default 60s) — increase further for large search lists.
- Aborting the refresh cycle on first anti-bot detection (already implemented).

With ~50 tracked searches even 60s between queries may still trigger DataDome over time.
A working strategy from community testing: 3 queries per run, 60s apart, every 15 minutes.

## Output channels

`notifications.build_channels(cfg, token)` returns the enabled channels, each with
`name`, `accepts(user, is_default)` and `send(user, messages)`:

| Channel | Enabled when | Receives |
| --- | --- | --- |
| `telegram` | token set, no `--tgoff` | every user with a `telegram_chat_id` (HTTP POST, plain-text fallback if Markdown fails) |
| `ntfy` | server + topic set, no `--ntfyoff` | default user only |
| `windows` | on Windows with `win10toast` installed, no `--winoff` | default user only |

To add a channel: a class with the same shape, an entry in `build_channels()`, an
`--<name>off` flag in `cli.py` and a field in `AppConfig`.

## Scraper runtime

`scraper.Scraper(cfg, token)` is used by every mode:

- `run(searches, wait=True)`: takes `lock`, runs `run_searches()`, delivers each user's
  messages to the channels that accept that user, pauses on bot detection and notifies
  every user with searches. Returns `(new items, blocked)` or `None` if paused or, with
  `wait=False`, busy
- `loop()`: polls every user's searches inside the active window, honours the pause
- `lock` (`threading.Lock`) ensures one scraping run at a time: DataDome tracks the
  IP, not the user

## Bot mode

The bot (`python-telegram-bot` v22, `concurrent_updates`) only handles commands:
`/list`, `/add`, `/delete`, `/refresh`, `/status`, `/help`. `Scraper.loop()` runs in a
daemon thread started in `post_init`; `/refresh` and the initial fetch of `/add` call
`Scraper.run(..., wait=False)` through `asyncio.to_thread`. Notifications reach
Telegram through the `telegram` channel, never through PTB.
