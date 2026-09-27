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


def transient_status(status: int) -> bool:
    return status == 429 or status >= 500


def error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("error_user_msg") or err.get("message") or str(err)
            code = err.get("code")
            return f"{msg} (code {code})" if code else msg
        if isinstance(err, str):
            return err
        if "message" in data:
            return str(data["message"])
    return str(data)[:300]


def check(resp: requests.Response, context: str) -> dict[str, Any]:
    """Return JSON for a 2xx response, otherwise raise PublishError."""
    if resp.status_code >= 400:
        transient = transient_status(resp.status_code)
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict) and err.get("is_transient"):
                transient = True
        except (ValueError, AttributeError):
            pass
        raise PublishError(f"{context}: {error_message(resp)}", transient=transient)
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def call(fn: Callable[..., requests.Response], *args: Any, context: str, **kwargs: Any) -> dict[str, Any]:
    """Run a requests call, turning network failures into transient PublishErrors."""
    try:
        resp = fn(*args, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise PublishError(f"{context}: network error ({exc})", transient=True) from exc
    return check(resp, context)
