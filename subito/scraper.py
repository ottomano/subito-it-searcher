"""Core scraping logic: the most important file in the project.

run_query() is the full cycle for one search:
  HTTP fetch, BeautifulSoup parse, find __NEXT_DATA__ script tag,
  walk JSON to items.list, extract title/price/location/shipping,
  diff against the items stored in SQLite, return new items as message strings.

If subito.it changes its page structure, this is where you fix it. The JSON
path is: props -> pageProps -> initialState -> items -> originalList.

run_searches() runs a list of searches (of any users) with query_delay between them
and groups the new messages by user id.

Scraper is the runtime built on top: it owns the output channels (notifications.py),
delivers each user's new items to them, keeps the polling loop and the anti-bot pause,
and makes sure only one run hits subito.it at a time (DataDome looks at the IP, not at
the user). The headless loop, --refresh and the Telegram bot all go through it.

HEADERS at the top is the browser impersonation block. Do not change it
unless the site starts blocking requests.

Extracted fields per item:
  title, link, date, location, price
  shipping        bool  /item_shippable key "1"
  tuttosubito     bool  /item_shipping_type == "Spedizione con TuttoSubito"
  verified_dealer bool  proTransactionsEnabled + tuttosubito
"""

from datetime import datetime, timedelta
from datetime import time as dtime
import json
import logging
import threading
import time

from bs4 import BeautifulSoup
from curl_cffi import requests

from subito import notifications, storage
from subito.config import AppConfig

logger = logging.getLogger(__name__)

# Browser-like headers to avoid bot detection on subito.it
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",  # noqa: E501
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",  # noqa: E501
}


def run_query(search: dict) -> list[str] | None:
    """Fetch one search page, diff against stored items, return new item messages.

    Stores new items and removes sold ones. On the first fetch of a search
    (last_checked NULL) items are stored but no messages are returned, so a new search
    does not flood the chat.
    Returns None if an anti-bot challenge was detected (caller should abort the run).
    """
    name = search["name"]
    url = search["url"]
    min_price = search["min_price"]  # int or None
    max_price = search["max_price"]  # int or None
    tuttosubito_only = bool(search["tuttosubito_only"])
    seeding = search["last_checked"] is None
    known = storage.item_links(search["id"])
    new_items = {}
    sold_links = set()

    logger.info(f'Running query "{name}" - {url}')

    msg = []

    # Fetch and parse
    page = requests.get(url, headers=HEADERS, impersonate="chrome124", timeout=15)

    _antibot = ("captcha-delivery.com", "Please enable JS", "dd.js")
    if page.status_code == 403 or any(m in page.text for m in _antibot):
        logger.warning(
            "Bot detection triggered. "
            "Stop the script, wait a few hours, open subito.it in a browser and "
            "solve any captcha, then restart."
        )
        return None

    soup = BeautifulSoup(page.text, "html.parser")

    script_tag = soup.find("script", id="__NEXT_DATA__")
    if not script_tag:
        logger.warning(
            f"Could not find JSON data on page for '{name}'. "
            "Aborting cycle — possible anti-bot challenge or site structure change."
        )
        return None

    json_data = json.loads(script_tag.string)

    # Path into Next.js hydration data where subito.it stores search results
    try:
        items_list = json_data["props"]["pageProps"]["initialState"]["items"][
            "originalList"
        ]
    except KeyError:
        items_list = []

    # Process each item
    for product in items_list:

        try:
            item_key = product.get("urn")
            if not item_key:
                continue

            title = product.get("subject", "No Title")
            link = product.get("urls", {}).get("default", "")
            raw_date = product.get("date", "")
            try:
                date = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S").strftime(
                    "%d-%m-%Y %H:%M:%S"
                )
            except ValueError:
                date = raw_date

            location = (
                product.get("geo", {}).get("town", {}).get("value", "Unknown town")
                + " ("
                + product.get("geo", {})
                .get("city", {})
                .get("shortName", "Unknown province")
                + ")"
            )

            # Price
            raw_price = None
            price = "Unknown price"
            features = product.get("features", {})
            price_feature = features.get("/price")
            if price_feature and "values" in price_feature:
                raw_price = price_feature["values"][0].get("key")
            if raw_price:
                try:
                    price = int(raw_price)
                except ValueError:
                    pass

            # Shipping
            shippable_feature = features.get("/item_shippable")
            shipping = (
                shippable_feature is not None
                and shippable_feature.get("values", [{}])[0].get("key") == "1"
            )

            # TuttoSubito: shipping handled by subito.it (buyer protection)
            shipping_type = (
                features.get("/item_shipping_type", {})
                .get("values", [{}])[0]
                .get("value", "")
            )
            tuttosubito = shipping_type == "Spedizione con TuttoSubito"

            # Verified dealer: registered shop + TuttoSubito transaction
            advertiser = product.get("advertiser", {})
            verified_dealer = (
                advertiser.get("proTransactionsEnabled") is True and tuttosubito
            )

            is_sold = product.get("sold", False)

        except Exception as e:
            logger.warning(f"Skipped item in '{name}': {e}")
            continue

        # Remove sold items from cache
        if is_sold:
            if link in known:
                sold_links.add(link)
            continue

        # Filters
        if tuttosubito_only and not tuttosubito:
            continue
        if min_price is not None and price != "Unknown price" and price < min_price:
            continue
        if max_price is not None and price != "Unknown price" and price > max_price:
            continue

        if link not in known and link not in new_items:
            badges = []
            if tuttosubito:
                badges.append("TuttoSubito")
            if verified_dealer:
                badges.append("Rivenditore verificato")
            badge_str = f" [{', '.join(badges)}]" if badges else ""

            tmp = (
                f"{date}\n"
                f"*{title}*\n"
                f"€ {price}{badge_str}\n"
                f"{location}\n"
                f"{link}\n"
            )
            msg.append(tmp)
            new_items[link] = {
                "title": title,
                "price": price,
                "location": location,
                "date": date,
                "shipping": shipping,
                "tuttosubito": tuttosubito,
                "verified_dealer": verified_dealer,
            }
            logger.debug(f"New result: {title} - {price} - {location}")

    storage.save_query_result(search["id"], new_items, sold_links)

    if seeding:
        logger.info(f"Stored {len(new_items)} initial item(s) for '{name}'.")
        return []
    if msg:
        logger.info(f"{len(msg)} new item(s) found for '{name}'.")
    else:
        logger.info(f"No new items for '{name}'.")
    return msg


def run_searches(
    searches: list[dict], cfg: AppConfig
) -> tuple[dict[int, list[str]], bool]:
    """Run searches in order, waiting query_delay between them.

    Returns (messages grouped by user id, blocked). blocked is True when an anti-bot
    challenge aborted the run; messages found before that are still returned, since
    their items are already stored. A network error skips only that search.
    """
    by_user: dict[int, list[str]] = {}
    for i, search in enumerate(searches):
        if i > 0 and cfg.query_delay > 0:
            logger.debug(f"Waiting {cfg.query_delay}s before next query.")
            time.sleep(cfg.query_delay)
        try:
            msgs = run_query(search)
        except requests.exceptions.RequestException as e:
            logger.warning(f"Network error on '{search['name']}': {e}")
            continue
        except Exception:
            logger.exception(f"Unexpected error on '{search['name']}'")
            continue
        if msgs is None:
            logger.warning("Aborting refresh cycle to protect IP reputation.")
            return by_user, True
        if msgs:
            by_user.setdefault(search["user_id"], []).extend(msgs)
    return by_user, False


def _in_between(now: dtime, start: dtime, end: dtime) -> bool:
    if start < end:
        return start <= now < end
    elif start == end:
        return True
    else:
        return start <= now or now < end


class Scraper:
    """Run searches and deliver new items to every output channel. Thread-safe."""

    def __init__(self, cfg: AppConfig, token: str | None):
        self.cfg = cfg
        self.channels = notifications.build_channels(cfg, token)
        self.lock = threading.Lock()
        self.paused_until: datetime | None = None
        self.last_run: datetime | None = None

    def paused(self) -> datetime | None:
        """Return the end of the anti-bot pause, if one is in progress."""
        until = self.paused_until
        return until if until and until > datetime.now() else None

    def run(self, searches: list[dict], wait: bool = True) -> tuple[int, bool] | None:
        """Run searches and deliver the results.

        Returns (number of new items, blocked), or None when nothing ran because of
        the anti-bot pause or, with wait=False, because another run is in progress.
        """
        if self.paused():
            return None
        if not self.lock.acquire(blocking=wait):
            return None
        try:
            by_user, blocked = run_searches(searches, self.cfg)
            self.deliver(by_user)
            self.last_run = datetime.now()
            if blocked:
                self._pause()
            return sum(len(m) for m in by_user.values()), blocked
        finally:
            self.lock.release()

    def deliver(self, by_user: dict[int, list[str]]) -> None:
        if not by_user:
            return
        default_id = storage.default_user()["id"]
        for user_id, msgs in by_user.items():
            user = storage.get_user(user_id)
            if user is None:  # deleted while the run was in progress
                continue
            for channel in self.channels:
                if channel.accepts(user, user_id == default_id):
                    try:
                        channel.send(user, msgs)
                    except Exception:
                        logger.exception(f"Channel {channel.name} failed")

    def _pause(self) -> None:
        until = datetime.now() + timedelta(seconds=self.cfg.bot_detection_sleep)
        self.paused_until = until
        text = (
            f"Bot detection triggered. Polling paused until {until:%Y-%m-%d %H:%M}, "
            "then it will resume automatically."
        )
        logger.warning(text)
        users = {s["user_id"] for s in storage.list_searches()}
        self.deliver({user_id: [text] for user_id in users})

    def loop(self) -> None:
        """Poll every user's searches forever, inside the active window."""
        cfg = self.cfg
        while True:
            if until := self.paused():
                time.sleep((until - datetime.now()).total_seconds())
                continue
            now = datetime.now().time()
            if _in_between(now, dtime(cfg.active_hour), dtime(cfg.pause_hour)):
                # An exception escaping here would stop polling for good (in bot mode
                # silently, while commands keep working). Skip the cycle instead.
                try:
                    self.run(storage.list_searches())
                except Exception:
                    logger.exception("Polling cycle failed, retrying after the delay")
                logger.info(f"Next poll in {cfg.delay} seconds.")
            time.sleep(cfg.delay)
