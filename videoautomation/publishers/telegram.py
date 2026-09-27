"""Post to a Telegram channel with a bot you add as a channel admin."""

from __future__ import annotations

from pathlib import Path

from ..errors import ConfigError, PublishError
from ..models import PostJob, PostResult
from ..telegram_api import UPLOAD_LIMIT, TelegramAPI, message_link
from .base import Publisher


class TelegramPublisher(Publisher):
    name = "telegram"

    def api(self) -> TelegramAPI:
        s = self.settings
        if not s.telegram_bot_token or not s.telegram_channel_id:
            raise ConfigError("Telegram posting needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID")
        return TelegramAPI(s.telegram_bot_token, s.telegram_api_base, self.session)

    def publish(self, job: PostJob) -> PostResult:
        api = self.api()
        channel = self.settings.telegram_channel_id
        paths = [Path(m.path) for m in job.media]
        too_big = [p.name for p in paths if p.stat().st_size > UPLOAD_LIMIT]
        if too_big and "api.telegram.org" in self.settings.telegram_api_base:
            raise PublishError(f"Telegram bots can upload up to 50 MB; too big: {', '.join(too_big)}")
        caption = job.caption or None
        if len(paths) == 1:
            if job.media[0].kind == "video":
                message = api.send_video(channel, paths[0], caption)
            else:
                message = api.send_photo(channel, paths[0], caption)
        else:
            kinds = ["video" if m.kind == "video" else "photo" for m in job.media]
            messages = api.send_media_group(channel, list(zip(kinds, paths)), caption)
            message = messages[0] if messages else {}
        message_id = message.get("message_id")
        url = message_link(message.get("chat", {}), message_id) if message_id else None
        return PostResult(job.platform, job.surface, "published", url=url,
                          remote_id=str(message_id) if message_id else None)
