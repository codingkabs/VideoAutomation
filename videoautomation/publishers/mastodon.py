"""Mastodon (any instance) with an access token from Preferences > Development."""

from __future__ import annotations

import time
from pathlib import Path

from ..errors import ConfigError, PublishError
from ..models import PostJob, PostResult
from .base import Publisher, call

MEDIA_TIMEOUT = 10 * 60


class MastodonPublisher(Publisher):
    name = "mastodon"

    def publish(self, job: PostJob) -> PostResult:
        s = self.settings
        if not s.mastodon_instance or not s.mastodon_access_token:
            raise ConfigError("Mastodon needs MASTODON_INSTANCE and MASTODON_ACCESS_TOKEN")
        headers = {"Authorization": f"Bearer {s.mastodon_access_token}"}
        media_ids = []
        for media in job.media[:4]:
            path = Path(media.path)
            with path.open("rb") as fh:
                uploaded = call(self.session.post, f"{s.mastodon_instance}/api/v2/media", headers=headers,
                                files={"file": (path.name, fh)}, timeout=600, context="Mastodon upload")
            media_id = uploaded["id"]
            deadline = time.monotonic() + MEDIA_TIMEOUT
            while not uploaded.get("url"):  # videos are processed asynchronously
                if time.monotonic() > deadline:
                    raise PublishError("Mastodon is still processing the video", transient=True)
                self.sleep(5)
                uploaded = call(self.session.get, f"{s.mastodon_instance}/api/v1/media/{media_id}",
                                headers=headers, timeout=60, context="Mastodon media status")
            media_ids.append(media_id)
        status = call(self.session.post, f"{s.mastodon_instance}/api/v1/statuses", headers=headers,
                      data={"status": job.caption, "media_ids[]": media_ids}, timeout=60,
                      context="Mastodon post")
        return PostResult(job.platform, job.surface, "published", url=status.get("url"),
                          remote_id=status.get("id"))
