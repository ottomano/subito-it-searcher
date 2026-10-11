"""Output channels: where new items are delivered. Independent of each other.

Each channel has:
  name                       used in logs
  accepts(user, is_default)  whether this user's items go to this channel
  send(user, messages)       deliver the messages

build_channels() returns the channels enabled by configuration and flags; scraper.py
sends every user's new items to every channel that accepts that user. To add a channel,
write a class with the same shape, add it to build_channels() and give it an
--<name>off flag in cli.py.

  telegram  HTTP POST to the user's telegram_chat_id (every user that has one)
  ntfy      global server + topic, default user only
  windows   toast on the machine running the scraper, default user only
"""

import logging
import platform

import requests

from subito import storage
from subito.config import AppConfig

logger = logging.getLogger(__name__)


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str):
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"

    def accepts(self, user: dict, is_default: bool) -> bool:
        return user.get("telegram_chat_id") is not None

    def send(self, user: dict, messages: list[str]) -> None:
        chat_id = user["telegram_chat_id"]
        for msg in messages:
            body = {"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}
            try:
                r = requests.post(self._url, json=body, timeout=15)
                if r.status_code == 400:
                    # Titles with unbalanced * or _ break Markdown; resend as plain text
                    del body["parse_mode"]
                    r = requests.post(self._url, json=body, timeout=15)
                if not r.ok:
                    desc = r.json().get("description", r.status_code)
                    logger.error(f"Telegram refused message to {chat_id}: {desc}")
            except requests.RequestException as e:
                # The exception text contains the URL, and with it the token
                logger.error(f"Failed to send Telegram message: {type(e).__name__}")


class NtfyChannel:
    name = "ntfy"

    def __init__(self, server: str, topic: str):
        self._url = f"{server.rstrip('/')}/{topic}"

    def accepts(self, user: dict, is_default: bool) -> bool:
        return is_default

    def send(self, user: dict, messages: list[str]) -> None:
        for msg in messages:
            try:
                requests.post(self._url, data=msg.encode("utf-8"), timeout=15)
            except Exception as e:
                logger.error(f"Failed to send ntfy notification: {e}")


class WindowsChannel:
    name = "windows"

    def __init__(self, toaster):
        self._toaster = toaster

    def accepts(self, user: dict, is_default: bool) -> bool:
        return is_default

    def send(self, user: dict, messages: list[str]) -> None:
        self._toaster.show_toast(
            "New announcements", f"{len(messages)} new item(s)", threaded=True
        )


def _windows_toaster():
    if platform.system() != "Windows":
        return None
    try:
        from win10toast import ToastNotifier
    except ImportError:
        logger.info("win10toast not installed, Windows notifications disabled")
        return None
    return ToastNotifier()


def build_channels(cfg: AppConfig, token: str | None) -> list:
    channels = []
    if token and not cfg.tgoff:
        channels.append(TelegramChannel(token))
    if not cfg.ntfyoff and (ntfy := storage.get_ntfy()):
        channels.append(NtfyChannel(*ntfy))
    if not cfg.winoff and (toaster := _windows_toaster()):
        channels.append(WindowsChannel(toaster))
    return channels
