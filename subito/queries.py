"""Search display and argument helpers shared by the CLI and the bot.

Public functions:
  to_price()         parse an optional price argument into int or None
  format_searches()  format search list as a string (used by CLI and bot)
  print_list()       print full list with all found items (`list` subcommand)
  print_list_short() print compact search summary (`list --short`)

print_list() and print_list_short() use plain print() intentionally:
they are user-facing display functions, not log events.
"""

from subito import storage


def to_price(value) -> int | None:
    if value is None or value == "null":
        return None
    return int(value)


def format_searches(searches: list[dict], bold: bool = False) -> str:
    """Return a compact formatted list of searches.

    bold=True wraps search names in Telegram markdown (*name*).
    Returns empty string if there are no searches.
    """
    lines = []
    for i, search in enumerate(searches, 1):
        label = f"*{search['name']}*" if bold else search["name"]
        parts = []
        if search["min_price"] is not None:
            parts.append(f"min €{search['min_price']}")
        if search["max_price"] is not None:
            parts.append(f"max €{search['max_price']}")
        if search["tuttosubito_only"]:
            parts.append("tuttosubito")
        filters = f"  filters: {', '.join(parts)}" if parts else ""
        lines.append(f"\n{i}) {label}  {search['url']}{filters}\n")
    return "".join(lines)


def print_list_short(searches: list[dict]) -> None:
    print(format_searches(searches))


def print_list(searches: list[dict]) -> None:
    for search in searches:
        print(f"\nsearch: {search['name']}")
        print(f"url: {search['url']}")
        for item in storage.list_items(search["id"]):
            price = item["price"] if item["price"] is not None else "?"
            print(f"\n  {item['title']} : {price}€ --> {item['location']}")
            print(f"  {item['link']}")
