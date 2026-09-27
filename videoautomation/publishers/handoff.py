"""Hand-off for phone-only apps (Lemon8, Kwai, Moj, ...), and fallback for any platform.

The ready-to-post file and caption go to your phone through your Telegram bot
(TELEGRAM_BOT_TOKEN + TELEGRAM_OWNER_CHAT_ID), and are always saved to the
outbox folder. Posting then takes a few taps: save the video, open the app,
paste the caption.
"""

from __future__ import annotations

import html
import shutil
from pathlib import Path

from ..config import platform_spec
from ..errors import PublishError
from ..models import PostJob, PostResult
from ..telegram_api import UPLOAD_LIMIT, TelegramAPI
from .base import Publisher


class HandoffPublisher(Publisher):
    name = "handoff"

    def outbox(self, job: PostJob) -> Path:
        folder = self.settings.outbox_dir / job.post_id / job.platform
        folder.mkdir(parents=True, exist_ok=True)
        for i, media in enumerate(job.media):
            src = Path(media.path)
            dest = folder / (f"{job.platform}_{i + 1:02d}{src.suffix}" if len(job.media) > 1
                             else f"{job.platform}{src.suffix}")
            if not dest.exists():
                try:
                    dest.hardlink_to(src)
                except OSError:
                    shutil.copy2(src, dest)
        (folder / "caption.txt").write_text(job.caption, encoding="utf-8")
        if job.options.get("title"):
            (folder / "title.txt").write_text(job.options["title"], encoding="utf-8")
        return folder

    def send_to_phone(self, job: PostJob, folder: Path) -> list[str]:
        s = self.settings
        api = TelegramAPI(s.telegram_bot_token, s.telegram_api_base, self.session)
        chat = s.telegram_owner_chat_id
        name = platform_spec(job.platform).get("name", job.platform)
        notes = []
        api.send_message(chat, f"📲 Ready to post on {name}. Save the file, open the app, paste the caption below.")
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".mp4", ".jpg", ".jpeg", ".png"))
        small = [p for p in files if p.stat().st_size <= UPLOAD_LIMIT]
        for path in small[:10]:
            api.send_document(chat, path)  # sent as files so Telegram does not recompress them
        if len(small) < len(files):
            notes.append(f"{len(files) - len(small)} file(s) over 50 MB stayed in {folder}")
        if job.options.get("title"):
            api.send_message(chat, f"<b>Title</b>\n<pre>{html.escape(job.options['title'])}</pre>", parse_mode="HTML")
        if job.caption:
            api.send_message(chat, f"<pre>{html.escape(job.caption)}</pre>", parse_mode="HTML")
        return notes

    def publish(self, job: PostJob) -> PostResult:
        folder = self.outbox(job)
        s = self.settings
        if s.telegram_bot_token and s.telegram_owner_chat_id:
            try:
                notes = self.send_to_phone(job, folder)
            except PublishError as exc:
                return PostResult(job.platform, job.surface, "handoff",
                                  notes=[f"saved to {folder}", f"could not send to Telegram: {exc}"])
            return PostResult(job.platform, job.surface, "handoff",
                              notes=["sent to your phone via Telegram; post it from the app"] + notes)
        return PostResult(job.platform, job.surface, "handoff",
                          notes=[f"saved to {folder}; post it from the app",
                                 "set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID to get it on your phone"])
