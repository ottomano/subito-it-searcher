# subito-it-searcher - Claude Code context

## What this project does

Scrapes subito.it (Italian classifieds) for search queries, diffs results against a local
cache, and pushes new listings via Telegram and/or ntfy. Runs as a continuous polling loop
(default), a one-shot refresh, or an interactive Telegram bot.

## Running the project

```bash
pip3 install -r requirements.txt
python3 subito-searcher.py --help
python3 subito-searcher.py <subcommand> --help
```

No build step. State files are created automatically under `data/` on first run.

## Module map

| File | Role |
| --- | --- |
| `subito-searcher.py` | Entry point: parse args, load state, dispatch |
| `subito/scraper.py` | Core: fetch, parse, diff. First file to check when the site breaks |
| `subito/notifications.py` | Telegram and ntfy channels, independent of each other |
| `subito/bot.py` | Interactive Telegram bot daemon (python-telegram-bot v22) |
| `subito/queries.py` | add, delete, print helpers |
| `subito/storage.py` | Load/save JSON state + env var overlay + `migrate_queries()` |
| `subito/cli.py` | Subcommands and top-level flags |
| `subito/config.py` | `DATA_DIR`, file paths, `AppConfig` dataclass |

## CLI structure

**Subcommands** (execute and exit):

```bash
python3 subito-searcher.py add <name> <url> [--min-price N] [--max-price N] [--tuttosubito]
python3 subito-searcher.py delete <name>
python3 subito-searcher.py list [--short]
python3 subito-searcher.py migrate
python3 subito-searcher.py setup [--telegram-token T] [--telegram-chat-id ID]
                                  [--ntfy-server S] [--ntfy-topic T]
```

**Top-level flags** (run modes):

```bash
python3 subito-searcher.py              # default: start polling loop
python3 subito-searcher.py --refresh    # run once and exit
python3 subito-searcher.py --bot        # interactive Telegram bot
python3 subito-searcher.py --delay 30 --active-hour 8 --pause-hour 23
python3 subito-searcher.py --query-delay 60        # seconds between queries (default: 60)
python3 subito-searcher.py --bot-detection-sleep 86400  # sleep on bot detection (default: 86400)
```

**Notification flags** (per-run):
`--tgoff`, `--ntfyoff`, `--winoff`

## Configuration priority

```text
CLI flags > env vars > config files > defaults
```

Environment variables:

| Variable | Description |
| --- | --- |
| `TELEGRAM_TOKEN` | Telegram bot API token |
| `TELEGRAM_CHAT_ID` | Telegram chat id |
| `NTFY_SERVER` | ntfy server URL |
| `NTFY_TOPIC` | ntfy topic |
| `DELAY` | Polling interval in seconds (default: 120) |
| `ACTIVE_HOUR` | Hour when to start polling in 24h notation |
| `PAUSE_HOUR` | Hour when to stop polling in 24h notation |
| `QUERY_DELAY` | Seconds to wait between queries to avoid anti-bot detection (default: 60) |
| `BOT_DETECTION_SLEEP` | Seconds to sleep after bot detection (default: 86400) |

## Data files

All files live in `data/` (gitignored, mounted as Docker volume):

| File | Content |
| --- | --- |
| `searches.json` | All saved searches and found items |
| `telegram.json` | Bot token and chat id |
| `ntfy.json` | Server URL and topic |
| `errors.log` | WARNING+ log records, rotating 1 MB x 3 backups |

## Data shape

```json
{
  "Auto": {
    "url": "https://www.subito.it/...",
    "min_price": 50,
    "max_price": 200,
    "shipping_only": false,
    "tuttosubito_only": true,
    "items": {
      "https://www.subito.it/item-link": {
        "title": "Fiat Punto",
        "price": 120,
        "location": "Milano (MI)",
        "date": "30-05-2026 12:56:20",
        "shipping": true,
        "tuttosubito": true,
        "verified_dealer": false
      }
    }
  }
}
```

- `min_price` / `max_price` are `int` or `null`, never the string `"null"`
- `tuttosubito_only` is a boolean, filter applied during scraping
- Use `&shp=true` in the URL for shipping-only results (native subito.it filter)
- `items` is keyed by the listing URL, one entry per unique listing found
- Each item stores: `title`, `price`, `location`, `date`, `shipping`, `tuttosubito`,
  `verified_dealer`
- Run `python3 subito-searcher.py migrate` once to convert files written by older versions

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
- No globals: state dicts loaded in `main()` and passed explicitly
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

## Notifications

Telegram and ntfy are checked independently in `run_query()`. Both fire if both are
configured. The `is_*_active()` guards in `notifications.py` check that credentials exist
and the off-flag is not set.

Known limitation: `send_telegram_messages()` builds a GET URL with message text inline,
which can break on special characters. A fix would switch to POST with a JSON body.

## Bot mode

`--bot` uses `python-telegram-bot` v22 (async). The polling loop runs as an
`asyncio.Task` inside the PTB event loop, no threads. Shared state lives in `app.bot_data`.
The same Telegram token handles both push notifications and command responses.
