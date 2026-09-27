"""Free, no-audit TikTok connection: sends videos to your TikTok drafts (inbox).

TikTok only lets audited apps publish publicly, but any app may upload to the
creator's inbox. You get a notification in TikTok, add a trending sound, and
post. Uploads go straight from disk, so no public storage is needed.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from .. import auth
from ..errors import PublishError
from ..models import PostJob, PostResult
from .base import Publisher, call

API = "https://open.tiktokapis.com/v2"
SINGLE_CHUNK_MAX = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024
STATUS_TIMEOUT = 5 * 60


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) under TikTok's rules: files up to 64 MB go
    in one chunk; larger files use 10 MB chunks and the last one takes the rest."""
    if size <= SINGLE_CHUNK_MAX:
        return size, 1
    return CHUNK, max(1, math.floor(size / CHUNK))


def _check(data: dict[str, Any], context: str) -> dict[str, Any]:
    error = data.get("error") or {}
    if error.get("code") not in (None, "ok"):
        transient = error.get("code") in ("rate_limit_exceeded", "internal_error")
        raise PublishError(f"{context}: {error.get('message') or error.get('code')}", transient=transient)
    return data.get("data") or {}


class TikTokDirectPublisher(Publisher):
    name = "tiktok"

    def publish(self, job: PostJob) -> PostResult:
        if job.surface == "photos" or job.media[0].kind != "video":
            raise PublishError("The direct TikTok connection sends video drafts only; photo posts need Zernio")
        token = auth.tiktok_token(self.settings, self.session)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"}
        path = Path(job.media[0].path)
        size = path.stat().st_size
        chunk_size, count = chunk_plan(size)
        init = _check(call(self.session.post, f"{API}/post/publish/inbox/video/init/", headers=headers, json={
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
                            "total_chunk_count": count},
        }, timeout=60, context="TikTok upload init"), "TikTok upload init")
        publish_id, upload_url = init.get("publish_id"), init.get("upload_url")
        if not publish_id or not upload_url:
            raise PublishError(f"TikTok did not return an upload URL: {init}")

        with path.open("rb") as fh:
            for index in range(count):
                start = index * chunk_size
                end = size - 1 if index == count - 1 else start + chunk_size - 1
                fh.seek(start)
                body = fh.read(end - start + 1)
                resp = self.session.put(upload_url, data=body, timeout=600, headers={
                    "Content-Type": "video/mp4", "Content-Length": str(len(body)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                })
                if resp.status_code >= 400:
                    raise PublishError(f"TikTok chunk {index + 1}/{count} failed ({resp.status_code})",
                                       transient=resp.status_code >= 500)

        deadline = time.monotonic() + STATUS_TIMEOUT
        while True:
            status = _check(call(self.session.post, f"{API}/post/publish/status/fetch/", headers=headers,
                                 json={"publish_id": publish_id}, timeout=60, context="TikTok status"),
                            "TikTok status")
            state = status.get("status")
            if state in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
                break
            if state == "FAILED":
                raise PublishError(f"TikTok rejected the upload: {status.get('fail_reason') or 'unknown reason'}")
            if time.monotonic() > deadline:
                return PostResult(job.platform, job.surface, "submitted", remote_id=publish_id,
                                  notes=["TikTok is still processing; the draft will appear in your inbox"])
            self.sleep(5)
        return PostResult(job.platform, job.surface, "draft", remote_id=publish_id,
                          notes=["check your TikTok inbox: open the notification, add a sound, and post"])
