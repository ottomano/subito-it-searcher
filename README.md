# subito-it-searcher

Monitor subito.it and get notified when new items match your searches.

## Features

- Continuous polling with configurable delay and active time window
- Price range filtering per query
- Independent output channels: **Telegram**, **[ntfy](https://ntfy.sh)**, Windows
  toast; use any combination
- Optional **Telegram bot** to manage searches from a chat, single or multi-user
- SQLite storage in a single file
- Docker support

## Setup

### Install dependencies

```bash
pip3 install -r requirements.txt
```

For Windows notifications, also install `win10toast`.

### Telegram

1. Create a bot via [@BotFather](https://t.me/botfather). It will give you an API token
2. Save the token and the chat (or `@channel`) that receives notifications, or set
   `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID`:

   ```bash
   python3 subito-searcher.py setup --telegram-token "YOUR_TOKEN" --telegram-chat-id "123456"
   ```

3. To keep the bot private, set `RESTRICT_BOT_TO_CHATID` to your chat id. If
   `TELEGRAM_CHAT_ID` is not set, notifications go to that same chat

Get your chat id from [@userinfobot](https://t.me/userinfobot), or start the bot with
`RESTRICT_BOT_TO_CHATID` set to any number and read
`Ignored update from unauthorized chat <id>` in the log.

| I want | Configuration |
| --- | --- |
| notifications in the chat where I command the bot | `RESTRICT_BOT_TO_CHATID=<my chat>` |
| commands in private, notifications on a channel | `RESTRICT_BOT_TO_CHATID=<my chat>`, `TELEGRAM_CHAT_ID=@channel` |
| Telegram only for notifications | `TELEGRAM_CHAT_ID=<chat>`, run with `--no-bot` |
| a bot open to anyone, each with own searches | no `RESTRICT_BOT_TO_CHATID` |

### ntfy

[ntfy](https://ntfy.sh) is a simple HTTP push notification service:

```bash
python3 subito-searcher.py setup --ntfy-server https://ntfy.sh --ntfy-topic your_topic
```

ntfy and Windows toasts receive the searches of the default user: the one of
`TELEGRAM_CHAT_ID` (or `RESTRICT_BOT_TO_CHATID`), or the local user when Telegram is
not configured at all.

### Environment variables

Env vars take priority over the values stored in the database.

|         Variable         |                              Description                              |
| ------------------------ | --------------------------------------------------------------------- |
| `TELEGRAM_TOKEN`         | Telegram bot API token                                                |
| `TELEGRAM_CHAT_ID`       | Chat or `@channel` receiving notifications                            |
| `RESTRICT_BOT_TO_CHATID` | Answer only this chat id; fallback for `TELEGRAM_CHAT_ID`             |
| `NTFY_SERVER`            | ntfy server URL                                                       |
| `NTFY_TOPIC`             | ntfy topic                                                            |
| `DELAY`                  | Polling interval in seconds (default: 120)                            |
| `ACTIVE_HOUR`            | Hour when to start polling in 24h notation                            |
| `PAUSE_HOUR`             | Hour when to stop polling in 24h notation                             |
| `QUERY_DELAY`            | Seconds between queries to avoid anti-bot detection (default: 60)     |
| `BOT_DETECTION_SLEEP`    | Seconds to pause after bot detection (default: 86400)                 |

### Docker

```bash
docker build -t subito-searcher .
docker run -v ./data:/app/data -e TELEGRAM_TOKEN=... subito-searcher <flags>
```

Docker Compose:

```yaml
services:
  subito-searcher:
    build: .
    volumes:
      - ./data:/app/data
    environment:
      - TELEGRAM_TOKEN=${TELEGRAM_TOKEN}
      - RESTRICT_BOT_TO_CHATID=${RESTRICT_BOT_TO_CHATID}
```

## Usage

```bash
python3 subito-searcher.py --help
python3 subito-searcher.py <subcommand> --help
```

### Run modes

```bash
# Poll all searches; with a Telegram token the bot also answers commands
python3 subito-searcher.py

# Custom polling interval and active time window
python3 subito-searcher.py --delay 300 --active-hour 8 --pause-hour 23

# Poll without answering Telegram commands
python3 subito-searcher.py --no-bot

# Run every search once, send notifications and exit
python3 subito-searcher.py --refresh
```

### Output channel flags

```bash
--tgoff    # Disable Telegram notifications (the bot still answers commands)
--ntfyoff  # Disable ntfy notifications
--winoff   # Disable Windows toast notifications
```

### Bot commands

| Command | Description |
| --- | --- |
| `/list` | Show your searches |
| `/add <name> <url> [min] [max] [tuttosubito]` | Add a search |
| `/delete <name>` | Remove a search |
| `/refresh` | Run your searches now |
| `/status` | Show state, last run, delay, active window, output channels |
| `/help` | List all commands |

The easiest way to add a search is to set up the filters on subito.it in the browser
and paste the resulting URL. When a search is added, its current listings are stored
without notifying them; from then on only new listings are sent.

### Manage searches from the command line

Without `--chat-id`, the commands act on the default user.

```bash
# Add a search (all filters optional)
python3 subito-searcher.py add Auto "https://www.subito.it/annunci-italia/vendita/usato/?q=auto"
python3 subito-searcher.py add Iphone "https://www.subito.it/annunci-italia/vendita/usato/?q=iphone" --min-price 50 --max-price 300
# Add with shipping (use native URL filter) + TuttoSubito buyer protection
python3 subito-searcher.py add Iphone "https://www.subito.it/annunci-italia/vendita/usato/?q=iphone&shp=true" --tuttosubito

# Remove a search
python3 subito-searcher.py delete Auto

# List searches
python3 subito-searcher.py list
python3 subito-searcher.py list --short
python3 subito-searcher.py list --all    # every user
```

### Migration

Versions before SQLite stored state in `searches.json`, `telegram.json` and `ntfy.json`.
Import them once; token, chat id and ntfy go to the settings, searches to the default
user (or to `--chat-id`):

```bash
python3 subito-searcher.py migrate
```

## Project structure

```text
subito-searcher.py      # Entry point: parse args, build config, dispatch
subito/
  scraper.py            # Core: fetch, parse, diff + Scraper runtime
  notifications.py      # Output channels: telegram, ntfy, windows
  bot.py                # Telegram command interface
  queries.py            # Formatting and print helpers
  storage.py            # SQLite schema and queries + legacy JSON import
  cli.py                # Subcommands and flags
  config.py             # Paths and AppConfig dataclass
data/                   # Gitignored, Docker volume
  subito.db
  errors.log
```

## Logs

Warnings and errors are written to `data/errors.log` (rotating, 1 MB max, 3 backups).
Check this file if searches stop returning results or items are being silently skipped.

## Troubleshooting

- **Bot does not answer**: check `RESTRICT_BOT_TO_CHATID`; updates from other chats
  are logged and ignored
- **No Telegram messages on a channel**: the bot must be an administrator of the
  channel
- **No items found**: subito.it embeds results in a `__NEXT_DATA__` script tag. If the
  site redesigns, update the JSON path in `subito/scraper.py`
- **Bot detection**: polling pauses for `BOT_DETECTION_SLEEP` seconds and every user
  with searches is told. Open subito.it in a browser and solve any captcha
