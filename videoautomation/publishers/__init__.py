"""Publisher factory."""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from ..models import PostJob, PostResult
from ..storage import Storage
from .base import Publisher
from .bluesky import BlueskyPublisher
from .browser import BrowserPublisher
from .handoff import HandoffPublisher
from .mastodon import MastodonPublisher
from .meta import FacebookPublisher, InstagramPublisher, ThreadsPublisher
from .sau import SauPublisher
from .telegram import TelegramPublisher
from .tiktok import TikTokDirectPublisher
from .zernio import ZernioPublisher

BACKENDS = {
    "zernio": ZernioPublisher,
    "threads": ThreadsPublisher,
    "bluesky": BlueskyPublisher,
    "telegram": TelegramPublisher,
    "mastodon": MastodonPublisher,
    "tiktok": TikTokDirectPublisher,
    "handoff": HandoffPublisher,
    "browser": BrowserPublisher,
    "sau": SauPublisher,
}


class DryRunPublisher(Publisher):
    """Describes the post instead of sending it."""

    name = "dry-run"
    remote_schedule = False

    def publish(self, job: PostJob) -> PostResult:
        media = ", ".join(f"{m.kind}:{m.path.rsplit('/', 1)[-1]}" for m in job.media)
        notes = [f"backend={job.backend}"]
        if media:
            notes += [f"media={media}", f"caption={len(job.caption)} chars"]
        for key in ("title", "trial_graduation", "draft", "thumb_offset_ms", "content_type", "tags",
                    "subreddit", "board_id"):
            if job.options.get(key) not in (None, False, [], ""):
                notes.append(f"{key}={job.options[key]}")
        return PostResult(job.platform, job.surface, "dry_run", run_at=job.run_at, notes=notes)


def make_publisher(backend: str, platform: str, settings: Settings, storage: Storage | None,
                   dry_run: bool = False) -> Publisher:
    if dry_run:
        return DryRunPublisher(settings, storage)
    if backend == "meta":
        if platform == "instagram":
            return InstagramPublisher(settings, storage)
        if platform == "facebook":
            return FacebookPublisher(settings, storage)
        raise ConfigError(f"The meta backend cannot post to {platform}")
    if backend == "insights":
        from ..insights import InsightsPublisher

        return InsightsPublisher(settings, storage)
    if backend in BACKENDS:
        return BACKENDS[backend](settings, storage)
    raise ConfigError(f"Unknown backend {backend!r} for {platform}")


__all__ = ["BACKENDS", "DryRunPublisher", "Publisher", "make_publisher", "ZernioPublisher",
           "InstagramPublisher", "FacebookPublisher", "HandoffPublisher"]
