"""Telegram bot: post from your phone.

Send the bot a video (or several photos) with the caption. It replies with a
control panel: toggle the Trial Reel, TikTok drafts, subtitles and timing,
preview, then tap Post. Links come back when it is done.

Private by default: only user ids in TELEGRAM_ALLOWED_USER_IDS are served.
Run it with `vauto bot` on a machine that stays on.
"""

from __future__ import annotations

import html
import time
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import notify, service
from .config import Settings, platform_spec
from .doctor import platform_ready
from .errors import PublishError, VautoError
from .scheduler import JobStore
from .telegram_api import TelegramAPI, download_limit

GROUP_SETTLE_SECONDS = 2.0
HELP = (
    "Send me a video (or up to 10 photos) with your caption and I'll post it everywhere.\n\n"
    "Tip: send videos as a <b>file</b> (📎 → File) so Telegram doesn't compress them.\n\n"
    "Commands:\n/posts  your recent posts and their numbers\n/stats  your last 7 days\n/status  what's set up\n"
    "/trials  Trial Reel results\n/cancel  drop the current post"
)


@dataclass
class ChatSession:
    files: list[Path] = field(default_factory=list)
    caption: str | None = None
    trial: bool = False
    tiktok_draft: bool = False
    drafts: bool = False
    best_time: bool = False
    subtitles: bool = False
    panel_id: int | None = None
    busy: bool = False
    preview: str = ""


class Bot:
    def __init__(self, settings: Settings, api: TelegramAPI | None = None, executor: Executor | None = None,
                 api_factory: Callable[[], TelegramAPI] | None = None):
        if not settings.telegram_bot_token:
            raise VautoError("Set TELEGRAM_BOT_TOKEN to run the bot (create one with @BotFather)")
        self.settings = settings
        self.api_factory = api_factory or (lambda: TelegramAPI(settings.telegram_bot_token,
                                                               settings.telegram_api_base))
        self.api = api or self.api_factory()
        self.executor = executor or ThreadPoolExecutor(max_workers=2)
        self.sessions: dict[int, ChatSession] = {}
        self.groups: dict[str, dict[str, Any]] = {}
        self.inbox = settings.home / "inbox"

    # ---------------------------------------------------------------- polling
    def run_forever(self) -> None:
        offset = None
        print("vauto bot running. Send it a video in Telegram. Ctrl+C to stop.")
        while True:
            try:
                offset = self.poll_once(offset)
            except PublishError as exc:
                print(f"telegram error: {exc}; retrying in 5s")
                time.sleep(5)

    def poll_once(self, offset: int | None, timeout: int = 25) -> int | None:
        updates = self.api.get_updates(offset, timeout=timeout)
        for update in updates:
            offset = update["update_id"] + 1
            try:
                self.handle_update(update)
            except VautoError as exc:
                chat = (update.get("message") or update.get("callback_query", {}).get("message") or {}).get("chat", {})
                if chat.get("id"):
                    self.api.send_message(chat["id"], f"⚠️ {exc}")
        self.flush_groups()
        return offset

    # --------------------------------------------------------------- dispatch
    def allowed(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.settings.telegram_allowed_user_ids

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            query = update["callback_query"]
            if self.allowed(query.get("from", {}).get("id")):
                self.handle_button(query)
            return
        message = update.get("message")
        if not message:
            return
        chat_id = message["chat"]["id"]
        user_id = message.get("from", {}).get("id")
        if not self.allowed(user_id):
            self.api.send_message(chat_id, f"This bot is private. Your Telegram user id is {user_id}.\n"
                                           f"Add TELEGRAM_ALLOWED_USER_IDS={user_id} to .env and restart the bot.")
            return
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            self.handle_command(chat_id, text.split()[0].split("@")[0].lower())
            return
        if self.media_of(message):
            self.receive_media(chat_id, message)
            return
        session = self.sessions.get(chat_id)
        if text and session and session.files and session.caption is None:
            session.caption = text
            self.show_panel(chat_id, new=True)
        elif text:
            self.api.send_message(chat_id, "Send a video or photos first, with the caption.")

    def handle_command(self, chat_id: int, command: str) -> None:
        if command in ("/start", "/help"):
            self.api.send_message(chat_id, HELP, parse_mode="HTML")
        elif command == "/cancel":
            self.sessions.pop(chat_id, None)
            self.api.send_message(chat_id, "Dropped. Send a new video whenever you like.")
        elif command == "/skip":
            session = self.sessions.get(chat_id)
            if session and session.files and session.caption is None:
                session.caption = ""
                self.show_panel(chat_id, new=True)
        elif command == "/status":
            lines = []
            for p in self.settings.default_platforms:
                ready, detail = platform_ready(self.settings, p)
                lines.append(f"{'✅' if ready else '⚠️'} {platform_spec(p)['name']}: {detail}")
            self.api.send_message(chat_id, "\n".join(lines) or "No platforms selected")
        elif command == "/jobs":
            if not self.settings.db_path.is_file():
                self.api.send_message(chat_id, "No posts yet.")
                return
            rows = JobStore(self.settings.db_path).rows(limit=10)
            lines = [f"{notify.ICONS.get(r.status, '•')} {r.job.label}: "
                     f"{(r.result.url or r.result.error) if r.result else r.run_at}" for r in rows]
            self.api.send_message(chat_id, "\n".join(lines) or "No posts yet.")
        elif command == "/trials":
            self.executor.submit(self._trials, chat_id)
        elif command == "/posts":
            self.api.send_message(chat_id, self._posts_text())
        elif command == "/stats":
            self.executor.submit(self._stats, chat_id)
        else:
            self.api.send_message(chat_id, HELP, parse_mode="HTML")

    # ------------------------------------------------------------------ media
    @staticmethod
    def media_of(message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("video"):
            return {**message["video"], "ext": ".mp4"}
        if message.get("photo"):
            return {**message["photo"][-1], "ext": ".jpg"}
        doc = message.get("document")
        if doc and str(doc.get("mime_type", "")).startswith(("video/", "image/")):
            name = doc.get("file_name") or ""
            ext = Path(name).suffix or (".mp4" if doc["mime_type"].startswith("video/") else ".jpg")
            return {**doc, "ext": ext}
        return None

    def _download(self, chat_id: int, media: dict[str, Any], index: int) -> Path:
        size = media.get("file_size") or 0
        if size > download_limit(self.settings.telegram_api_base):
            raise VautoError(
                f"That file is {size / 1e6:.0f} MB, over Telegram's 20 MB limit for bots. Share it to the vauto "
                "app instead, or turn on the big-files server (README > Use it from your phone).")
        folder = self.inbox / str(chat_id) / datetime.now().strftime("%Y%m%d-%H%M%S")
        return self.api.download(media["file_id"], folder / f"media_{index:02d}{media['ext']}")

    def receive_media(self, chat_id: int, message: dict[str, Any]) -> None:
        group = message.get("media_group_id")
        if group:
            entry = self.groups.setdefault(group, {"chat": chat_id, "messages": [], "last": 0.0})
            entry["messages"].append(message)
            entry["last"] = time.monotonic()
            return
        self.start_session(chat_id, [message])

    def flush_groups(self, force: bool = False) -> None:
        now = time.monotonic()
        for key in list(self.groups):
            entry = self.groups[key]
            if force or now - entry["last"] >= GROUP_SETTLE_SECONDS:
                del self.groups[key]
                self.start_session(entry["chat"], entry["messages"])

    def start_session(self, chat_id: int, messages: list[dict[str, Any]]) -> None:
        files = [self._download(chat_id, self.media_of(m), i) for i, m in enumerate(messages)]
        caption = next((m.get("caption") for m in messages if m.get("caption")), None)
        self.sessions[chat_id] = ChatSession(files=files, caption=caption, trial=self.settings.trial_default,
                                             drafts=self.settings.drafts_default)
        if caption is None:
            self.api.send_message(chat_id, f"Got {len(files)} file(s). Now send the caption (or /skip for none).")
        else:
            self.show_panel(chat_id, new=True)

    # ------------------------------------------------------------------ panel
    def panel(self, session: ChatSession) -> tuple[str, dict[str, Any]]:
        kind = "video" if session.files and session.files[0].suffix.lower() in (".mp4", ".mov", ".webm", ".mkv") \
            else f"{len(session.files)} photo(s)"
        platforms = ", ".join(platform_spec(p)["name"] for p in self.settings.default_platforms)
        caption = html.escape((session.caption or "(no caption)")[:600])
        lines = [
            f"<b>Ready to post</b> ({kind})",
            f"<b>To:</b> {html.escape(platforms)}",
            f"<b>Caption:</b>\n{caption}",
            "",
            f"🧪 Trial Reel: {'on' if session.trial else 'off'}",
            f"📝 Save as drafts: {'on (nothing is published yet)' if session.drafts else 'off'}",
            f"🎵 TikTok: {'send to drafts' if session.tiktok_draft or session.drafts else 'publish'}",
            f"⏰ When: {'next best time' if session.best_time else 'now'}",
            f"💬 Subtitles: {'auto' if session.subtitles else 'off'}",
        ]
        if session.preview:
            lines += ["", "<b>Preview</b>", f"<pre>{html.escape(session.preview[:1500])}</pre>"]
        keyboard = {"inline_keyboard": [
            [{"text": f"🧪 Trial {'✓' if session.trial else '✗'}", "callback_data": "trial"},
             {"text": f"🎵 Drafts {'✓' if session.tiktok_draft else '✗'}", "callback_data": "draft"}],
            [{"text": "⏰ Best time" if not session.best_time else "⏰ Now", "callback_data": "when"},
             {"text": f"💬 Subs {'✓' if session.subtitles else '✗'}", "callback_data": "subs"}],
            [{"text": f"📝 Drafts {'✓' if session.drafts else '✗'}", "callback_data": "drafts"}],
            [{"text": "👀 Preview", "callback_data": "preview"},
             {"text": "💾 Save drafts" if session.drafts else "🚀 Post", "callback_data": "post"}],
            [{"text": "✖ Cancel", "callback_data": "cancel"}],
        ]}
        return "\n".join(lines), keyboard

    def show_panel(self, chat_id: int, new: bool = False) -> None:
        session = self.sessions[chat_id]
        text, keyboard = self.panel(session)
        if new or session.panel_id is None:
            message = self.api.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
            session.panel_id = message.get("message_id")
        else:
            self.api.edit_message(chat_id, session.panel_id, text, reply_markup=keyboard, parse_mode="HTML")

    def handle_button(self, query: dict[str, Any]) -> None:
        chat_id = query["message"]["chat"]["id"]
        action = query.get("data")
        session = self.sessions.get(chat_id)
        if session is None:
            self.api.answer_callback(query["id"], "That post has finished. Send a new video.")
            return
        if session.busy:
            self.api.answer_callback(query["id"], "Still working on it…")
            return
        self.api.answer_callback(query["id"])
        if action == "cancel":
            self.sessions.pop(chat_id, None)
            self.api.edit_message(chat_id, query["message"]["message_id"], "Dropped. Nothing was posted.")
            return
        if action in ("trial", "draft", "when", "subs", "drafts"):
            attr = {"trial": "trial", "draft": "tiktok_draft", "when": "best_time", "subs": "subtitles",
                    "drafts": "drafts"}[action]
            setattr(session, attr, not getattr(session, attr))
            session.preview = ""
            self.show_panel(chat_id)
            return
        if action in ("preview", "post"):
            session.busy = True
            self.api.edit_message(chat_id, session.panel_id,
                                  "⏳ Preparing the preview…" if action == "preview" else "⏳ Posting…")
            self.executor.submit(self._run, chat_id, action == "preview")

    # ------------------------------------------------------------- background
    def _request(self, session: ChatSession, dry_run: bool):
        publish_at, note = service.resolve_publish_at(self.settings, "best" if session.best_time else None,
                                                      self.settings.default_platforms)
        req = service.make_request(self.settings, session.files, session.caption or "", None,
                                   trial=session.trial, tiktok_draft=session.tiktok_draft, drafts=session.drafts,
                                   subtitles="auto" if session.subtitles else None,
                                   publish_at=publish_at, dry_run=dry_run)
        return req, note

    def _run(self, chat_id: int, dry_run: bool) -> None:
        api = self.api_factory()
        session = self.sessions.get(chat_id)
        if session is None:
            return
        try:
            req, note = self._request(session, dry_run)
            plan, results = service.post(self.settings, req)
            lines = [notify.result_line(r) for r in results]
            if note:
                lines.insert(0, note)
            if dry_run:
                session.preview = "\n".join(lines + [f"note: {n}" for n in plan.notes][:8])
                session.busy = False
                text, keyboard = self.panel(session)
                api.edit_message(chat_id, session.panel_id, text, reply_markup=keyboard, parse_mode="HTML")
            else:
                self.sessions.pop(chat_id, None)
                api.edit_message(chat_id, session.panel_id, notify.results_text(results, f"Post {plan.post_id}"))
        except VautoError as exc:
            session.busy = False
            api.send_message(chat_id, f"⚠️ {exc}")
        except Exception as exc:  # keep the bot alive
            session.busy = False
            api.send_message(chat_id, f"⚠️ Unexpected error: {type(exc).__name__}: {exc}")

    def _posts_text(self, limit: int = 5) -> str:
        from .tracker import Tracker, compact, short

        if not self.settings.db_path.is_file():
            return "No posts yet. Send me a video to start."
        posts = Tracker.open(self.settings).posts(self.settings, limit=limit)["posts"]
        if not posts:
            return "No posts yet. Send me a video to start."
        blocks = []
        for p in posts:
            t = p["totals"]
            numbers = " · ".join(f"{compact(t[k])} {word}" for k, word in (("views", "views"), ("likes", "likes"))
                                 if t.get(k))
            lines = [f"🎬 {short(p['caption'], 60) or '(no caption)'}" + (f"\n   {numbers}" if numbers else "")]
            for j in p["jobs"]:
                status = j["status"].replace("_past", "")
                lines.append(f"   {notify.ICONS.get(status, '•')} {j['name']}"
                             + (f": {j['url']}" if j["url"] else f": {j['error']}" if j["error"] else ""))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks) + "\n\nAll posts and numbers: the vauto app → My posts."

    def _stats(self, chat_id: int) -> None:
        from .stats import refresh
        from .tracker import Tracker, digest_text

        api = self.api_factory()
        if not self.settings.db_path.is_file():
            api.send_message(chat_id, "No posts yet.")
            return
        tracker = Tracker.open(self.settings)
        try:
            refresh(self.settings, tracker, days=7)
        except Exception:  # numbers are best effort; show what is saved
            pass
        api.send_message(chat_id, digest_text(self.settings, tracker, days=7))

    def _trials(self, chat_id: int) -> None:
        from .insights import compare, trial_keys

        api = self.api_factory()
        if not self.settings.db_path.is_file():
            api.send_message(chat_id, "No Trial Reels yet.")
            return
        store = JobStore(self.settings.db_path)
        keys = trial_keys(store, 3)
        if not keys:
            api.send_message(chat_id, "No Trial Reels yet.")
        for key in keys:
            comparison = compare(self.settings, store, key)
            api.send_message(chat_id, f"<pre>{html.escape(chr(10).join(comparison.lines()))}</pre>", parse_mode="HTML")


def run_bot(settings: Settings) -> None:
    if not settings.telegram_allowed_user_ids:
        print("Note: TELEGRAM_ALLOWED_USER_IDS is empty. Message the bot once; it replies with your id to add.")
    try:
        Bot(settings).run_forever()
    except KeyboardInterrupt:
        pass
