"""Minimal Telegram Bot API client (requests only).

Used for posting to a channel, the phone hand-off, notifications and the bot.
Standard Bot API limits: bots upload files up to 50 MB and download files up
to 20 MB. A self-hosted Bot API server (TELEGRAM_API_BASE) lifts both to 2 GB.
"""

from __future__ import annotations

import json
import shutil
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import requests

from .errors import PublishError

UPLOAD_LIMIT = 50 * 1024 * 1024
DOWNLOAD_LIMIT = 20 * 1024 * 1024
LOCAL_SERVER_LIMIT = 2000 * 1024 * 1024
OFFICIAL_HOST = "api.telegram.org"


def upload_limit(base: str) -> int:
    """Bots upload 50 MB through Telegram's servers, 2 GB through a local Bot API server."""
    return UPLOAD_LIMIT if OFFICIAL_HOST in base else LOCAL_SERVER_LIMIT


def download_limit(base: str) -> int:
    return DOWNLOAD_LIMIT if OFFICIAL_HOST in base else LOCAL_SERVER_LIMIT


class TelegramAPI:
    def __init__(self, token: str, base: str = "https://api.telegram.org", session: requests.Session | None = None):
        if not token:
            raise PublishError("TELEGRAM_BOT_TOKEN is not set")
        self.token = token
        self.base = base.rstrip("/")
        self.session = session or requests.Session()

    # ------------------------------------------------------------ core call
    def call(self, method: str, data: dict[str, Any] | None = None,
             files: dict[str, tuple[str, Any]] | None = None, timeout: float = 120) -> Any:
        url = f"{self.base}/bot{self.token}/{method}"
        payload = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
                   for k, v in (data or {}).items() if v is not None}
        try:
            resp = self.session.post(url, data=payload, files=files, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise PublishError(f"Telegram {method}: network error ({exc})", transient=True) from exc
        try:
            body = resp.json()
        except ValueError:
            body = {"ok": False, "description": resp.text[:200]}
        if not body.get("ok"):
            code = body.get("error_code") or resp.status_code
            raise PublishError(f"Telegram {method}: {body.get('description', 'failed')} (code {code})",
                               transient=code == 429 or (isinstance(code, int) and code >= 500))
        return body.get("result")

    # ------------------------------------------------------------- sending
    def send_message(self, chat_id: str | int, text: str, reply_markup: dict | None = None,
                     parse_mode: str | None = None) -> dict:
        return self.call("sendMessage", {"chat_id": chat_id, "text": text[:4096], "parse_mode": parse_mode,
                                         "reply_markup": reply_markup, "disable_web_page_preview": True})

    def _send_file(self, method: str, field: str, chat_id: str | int, path: Path, caption: str | None,
                   extra: dict[str, Any] | None = None) -> dict:
        with path.open("rb") as fh:
            return self.call(method, {"chat_id": chat_id, "caption": (caption or None) and caption[:1024],
                                      **(extra or {})},
                             files={field: (path.name, fh)}, timeout=600)

    def send_video(self, chat_id: str | int, path: Path, caption: str | None = None, **extra: Any) -> dict:
        return self._send_file("sendVideo", "video", chat_id, path, caption, {"supports_streaming": True, **extra})

    def send_photo(self, chat_id: str | int, path: Path, caption: str | None = None) -> dict:
        return self._send_file("sendPhoto", "photo", chat_id, path, caption)

    def send_document(self, chat_id: str | int, path: Path, caption: str | None = None) -> dict:
        return self._send_file("sendDocument", "document", chat_id, path, caption)

    def send_media_group(self, chat_id: str | int, items: list[tuple[str, Path]], caption: str | None = None) -> list:
        """``items`` are (kind, path) with kind "photo" or "video"; up to 10."""
        media = []
        with ExitStack() as stack:
            files = {}
            for i, (kind, path) in enumerate(items[:10]):
                name = f"file{i}"
                files[name] = (path.name, stack.enter_context(path.open("rb")))
                entry: dict[str, Any] = {"type": kind, "media": f"attach://{name}"}
                if i == 0 and caption:
                    entry["caption"] = caption[:1024]
                media.append(entry)
            return self.call("sendMediaGroup", {"chat_id": chat_id, "media": media}, files=files, timeout=600)

    # ---------------------------------------------------- bot interaction
    def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 15)

    def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def edit_message(self, chat_id: str | int, message_id: int, text: str, reply_markup: dict | None = None,
                     parse_mode: str | None = None) -> None:
        self.call("editMessageText", {"chat_id": chat_id, "message_id": message_id, "text": text[:4096],
                                      "reply_markup": reply_markup, "parse_mode": parse_mode,
                                      "disable_web_page_preview": True})

    def download(self, file_id: str, dest: Path) -> Path:
        info = self.call("getFile", {"file_id": file_id})
        size = info.get("file_size") or 0
        if "file_path" not in info:
            raise PublishError("Telegram did not return a download path (file too large for the Bot API?)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        file_path = info["file_path"]
        if file_path.startswith("/"):
            # A local Bot API server (--local) returns a path on its own disk.
            source = Path(file_path)
            if not source.is_file():
                raise PublishError(
                    f"The Bot API server stored the file at {file_path}, which this machine cannot read. "
                    "Share the server's data folder with vauto (docker-compose does this for you)."
                )
            shutil.copyfile(source, dest)
            return dest
        url = f"{self.base}/file/bot{self.token}/{file_path}"
        try:
            with self.session.get(url, stream=True, timeout=600) as resp:
                if resp.status_code >= 400:
                    raise PublishError(f"Telegram download failed ({resp.status_code})", transient=True)
                with dest.open("wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
        except (requests.ConnectionError, requests.Timeout) as exc:
            raise PublishError(f"Telegram download: network error ({exc})", transient=True) from exc
        if size and dest.stat().st_size != size:
            raise PublishError("Telegram download was incomplete", transient=True)
        return dest


def message_link(chat: dict[str, Any], message_id: int) -> str | None:
    """Public link for a channel message, when the channel has one."""
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{message_id}"
    chat_id = str(chat.get("id", ""))
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None
