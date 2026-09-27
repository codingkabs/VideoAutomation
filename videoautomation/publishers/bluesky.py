"""Bluesky via the AT Protocol, using an app password (Settings > App passwords)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..errors import ConfigError, PublishError
from ..ffmpeg import probe
from ..models import MediaFile, PostJob, PostResult
from .base import Publisher, call

TAG_RE = re.compile(r"(?<![\w#])#([^\s#.,!?;:()\[\]]+)")
URL_RE = re.compile(r"https?://[^\s)]+")


def facets(text: str) -> list[dict[str, Any]]:
    """Rich-text facets so hashtags and links are clickable (byte offsets, UTF-8)."""
    out = []
    for match in URL_RE.finditer(text):
        start = len(text[:match.start()].encode("utf-8"))
        end = start + len(match.group(0).encode("utf-8"))
        out.append({"index": {"byteStart": start, "byteEnd": end},
                    "features": [{"$type": "app.bsky.richtext.facet#link", "uri": match.group(0)}]})
    for match in TAG_RE.finditer(text):
        start = len(text[:match.start()].encode("utf-8"))
        end = start + len(match.group(0).encode("utf-8"))
        out.append({"index": {"byteStart": start, "byteEnd": end},
                    "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": match.group(1)}]})
    return out


class BlueskyPublisher(Publisher):
    name = "bluesky"

    def _login(self) -> dict[str, Any]:
        s = self.settings
        if not s.bluesky_handle or not s.bluesky_app_password:
            raise ConfigError("Bluesky needs BLUESKY_HANDLE and BLUESKY_APP_PASSWORD")
        return call(self.session.post, f"{s.bluesky_pds}/xrpc/com.atproto.server.createSession",
                    json={"identifier": s.bluesky_handle, "password": s.bluesky_app_password},
                    timeout=30, context="Bluesky login")

    def _upload(self, jwt: str, media: MediaFile) -> dict[str, Any]:
        path = Path(media.path)
        mime = "video/mp4" if media.kind == "video" else "image/jpeg"
        with path.open("rb") as fh:
            data = call(self.session.post, f"{self.settings.bluesky_pds}/xrpc/com.atproto.repo.uploadBlob",
                        headers={"Authorization": f"Bearer {jwt}", "Content-Type": mime},
                        data=fh, timeout=600, context="Bluesky upload")
        if "blob" not in data:
            raise PublishError(f"Bluesky upload returned no blob: {data}")
        return data["blob"]

    @staticmethod
    def _aspect(media: MediaFile) -> dict[str, int]:
        try:
            info = probe(media.path)
            return {"width": info.width, "height": info.height}
        except Exception:
            return {"width": 1080, "height": 1920}

    def publish(self, job: PostJob) -> PostResult:
        session = self._login()
        jwt, did = session["accessJwt"], session["did"]
        handle = session.get("handle") or self.settings.bluesky_handle
        if job.media[0].kind == "video":
            embed: dict[str, Any] = {"$type": "app.bsky.embed.video", "video": self._upload(jwt, job.media[0]),
                                     "aspectRatio": self._aspect(job.media[0])}
        else:
            embed = {"$type": "app.bsky.embed.images", "images": [
                {"alt": "", "image": self._upload(jwt, m), "aspectRatio": self._aspect(m)} for m in job.media[:4]
            ]}
        record = {
            "$type": "app.bsky.feed.post",
            "text": job.caption,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "embed": embed,
        }
        found = facets(job.caption)
        if found:
            record["facets"] = found
        created = call(self.session.post, f"{self.settings.bluesky_pds}/xrpc/com.atproto.repo.createRecord",
                       headers={"Authorization": f"Bearer {jwt}"},
                       json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
                       timeout=60, context="Bluesky post")
        uri = created.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else None
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if rkey else None
        return PostResult(job.platform, job.surface, "published", url=url, remote_id=uri or None)
