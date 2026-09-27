"""Publisher factory."""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from ..models import PostJob, PostResult
from ..storage import Storage
from .base import Publisher
from .meta import FacebookPublisher, InstagramPublisher
from .zernio import ZernioPublisher


class DryRunPublisher(Publisher):
    """Describes the post instead of sending it."""

    name = "dry-run"
    remote_schedule = False

    def publish(self, job: PostJob) -> PostResult:
        media = ", ".join(f"{m.kind}:{m.path.rsplit('/', 1)[-1]}" for m in job.media)
        notes = [f"backend={job.backend}", f"media={media}", f"caption={len(job.caption)} chars"]
        for key in ("title", "trial_graduation", "draft", "thumb_offset_ms", "content_type", "tags"):
            if job.options.get(key) not in (None, False, [], ""):
                notes.append(f"{key}={job.options[key]}")
        return PostResult(job.platform, job.surface, "dry_run", run_at=job.run_at, notes=notes)


def make_publisher(backend: str, platform: str, settings: Settings, storage: Storage | None,
                   dry_run: bool = False) -> Publisher:
    if dry_run:
        return DryRunPublisher(settings, storage)
    if backend == "zernio":
        return ZernioPublisher(settings, storage)
    if backend == "meta":
        if platform == "instagram":
            return InstagramPublisher(settings, storage)
        if platform == "facebook":
            return FacebookPublisher(settings, storage)
        raise ConfigError(f"The meta backend cannot post to {platform}")
    raise ConfigError(f"Unknown backend {backend!r} for {platform}")


__all__ = ["DryRunPublisher", "Publisher", "make_publisher", "ZernioPublisher",
           "InstagramPublisher", "FacebookPublisher"]
