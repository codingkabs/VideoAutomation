"""Publisher interface and shared HTTP helpers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

import requests

from ..config import Settings
from ..errors import ConfigError, PublishError
from ..models import MediaFile, PostJob, PostResult
from ..storage import Storage
from ..http import call, check, error_message, transient_status  # noqa: F401 (re-exported)


class Publisher(ABC):
    """Posts one PostJob. ``remote_schedule`` publishers accept future ``run_at``
    values and schedule on the platform side instead of the local queue."""

    name = "base"
    remote_schedule = False

    def __init__(
        self,
        settings: Settings,
        storage: Storage | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.storage = storage
        self.session = session or requests.Session()
        self.sleep = sleep

    @abstractmethod
    def publish(self, job: PostJob) -> PostResult:
        ...

    def schedules_remotely(self, job: PostJob) -> bool:
        """True when a future ``run_at`` can be handed to the platform right away."""
        return self.remote_schedule

    def ensure_urls(self, job: PostJob, kinds: tuple[str, ...] = ("video", "image")) -> None:
        """Upload any media of the given kinds that has no public URL yet."""
        for media in job.media:
            if media.kind in kinds:
                self.ensure_url(media, job.post_id, job.platform)

    def ensure_url(self, media: MediaFile, post_id: str, platform: str) -> str:
        if media.url:
            return media.url
        if self.storage is None:
            raise ConfigError(
                f"{platform} needs media at a public URL. Configure storage "
                "(S3_BUCKET for R2/S3, or ZERNIO_API_KEY) - see README."
            )
        path = Path(media.path)
        media.url = self.storage.put(path, f"{post_id}/{path.name}")
        return media.url
