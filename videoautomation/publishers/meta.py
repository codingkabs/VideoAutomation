"""Direct Meta Graph API publishers: Instagram and Facebook Pages.

Videos upload straight from disk through Meta's resumable upload host
(rupload.facebook.com), so Reels and Trial Reels need no public storage.
Instagram photos must be fetched by Meta from a public URL.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import auth
from ..errors import ConfigError, PublishError
from ..models import MediaFile, PostJob, PostResult
from .base import Publisher, call

POLL_SECONDS = 5.0
PROCESS_TIMEOUT = 15 * 60


class _MetaBase(Publisher):
    name = "meta"

    @property
    def graph(self) -> str:
        s = self.settings
        return f"https://{s.meta_graph_host}/{s.meta_graph_version}"

    def _get(self, path: str, token: str, context: str, **params: Any) -> dict[str, Any]:
        return call(self.session.get, f"{self.graph}/{path}", params={**params, "access_token": token},
                    timeout=60, context=context)

    def _post(self, path: str, token: str, context: str, files: Any = None, **data: Any) -> dict[str, Any]:
        clean = {k: v for k, v in data.items() if v is not None}
        return call(self.session.post, f"{self.graph}/{path}", data={**clean, "access_token": token},
                    files=files, timeout=300, context=context)

    def _first_comment(self, object_id: str | None, token: str, job: PostJob, notes: list[str]) -> None:
        """Post the first comment under a published post. Best effort: the post stays up either way."""
        text = job.options.get("first_comment")
        if not text or not object_id:
            return
        try:
            self._post(f"{object_id}/comments", token, "first comment", message=text)
            notes.append("first comment added")
        except PublishError as exc:
            notes.append(f"first comment not added ({exc})")

    def _rupload(self, url: str, token: str, media: MediaFile, context: str) -> None:
        path = Path(media.path)
        size = path.stat().st_size
        headers = {"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}
        with path.open("rb") as fh:
            data = call(self.session.post, url, headers=headers, data=fh, timeout=1800, context=context)
        if data.get("success") is False:
            raise PublishError(f"{context}: upload not accepted ({data})", transient=True)


# --------------------------------------------------------------------- Instagram


class InstagramPublisher(_MetaBase):
    def _creds(self) -> tuple[str, str]:
        s = self.settings
        token = auth.instagram_token(s, self.session)
        if not s.ig_user_id or not token:
            raise ConfigError("Instagram direct posting needs IG_USER_ID and IG_ACCESS_TOKEN (see `vauto auth meta`)")
        return s.ig_user_id, token

    def publish(self, job: PostJob) -> PostResult:
        ig_id, token = self._creds()
        self._check_quota(ig_id, token)
        surface = job.surface
        if surface in ("reel", "trial_reel"):
            container = self._video_container(ig_id, token, job.media[0], "REELS", job)
        elif surface == "story":
            media = job.media[0]
            if media.kind == "video":
                container = self._video_container(ig_id, token, media, "STORIES", job)
            else:
                self.ensure_urls(job, ("image",))
                container = self._post(f"{ig_id}/media", token, "Instagram story container",
                                       media_type="STORIES", image_url=media.url)["id"]
        elif surface == "image":
            self.ensure_urls(job, ("image",))
            container = self._post(f"{ig_id}/media", token, "Instagram image container",
                                   image_url=job.media[0].url, caption=job.caption)["id"]
        elif surface == "carousel":
            container = self._carousel(ig_id, token, job)
        else:
            raise PublishError(f"Instagram cannot post surface {surface!r}")

        self._wait(container, token)
        media_id = self._post(f"{ig_id}/media_publish", token, "Instagram publish", creation_id=container)["id"]
        permalink = None
        try:
            permalink = self._get(media_id, token, "Instagram permalink", fields="permalink").get("permalink")
        except PublishError:
            pass
        notes = []
        if surface == "trial_reel":
            notes.append("trial reel: shown to non-followers first")
        self._first_comment(media_id, token, job, notes)
        return PostResult(job.platform, surface, "published", url=permalink, remote_id=media_id, notes=notes,
                          platform_post_id=media_id)

    def _check_quota(self, ig_id: str, token: str) -> None:
        """Fail early when the 24 h API publishing quota is used up (best effort)."""
        try:
            data = self._get(f"{ig_id}/content_publishing_limit", token, "Instagram quota",
                             fields="quota_usage,config")
        except PublishError:
            return
        entries = data.get("data") or []
        if not entries:
            return
        usage = entries[0].get("quota_usage")
        total = (entries[0].get("config") or {}).get("quota_total")
        if usage is not None and total and usage >= total:
            raise PublishError(f"Instagram API publishing limit reached ({usage}/{total} in 24 h)")

    def _video_container(self, ig_id: str, token: str, media: MediaFile, media_type: str, job: PostJob,
                         carousel_item: bool = False) -> str:
        opts = job.options
        params: dict[str, Any] = {"media_type": media_type, "upload_type": "resumable"}
        if carousel_item:
            params["is_carousel_item"] = "true"
        elif media_type == "REELS":
            params["caption"] = job.caption
            if opts.get("thumb_offset_ms") is not None:
                params["thumb_offset"] = int(opts["thumb_offset_ms"])
            if opts.get("audio_name"):
                params["audio_name"] = opts["audio_name"]
            if opts.get("trial_graduation"):
                params["trial_params"] = json.dumps({"graduation_strategy": opts["trial_graduation"]})
            else:
                params["share_to_feed"] = "true"
        created = self._post(f"{ig_id}/media", token, f"Instagram {media_type.lower()} container", **params)
        container = created["id"]
        upload_url = created.get("uri") or (
            f"https://rupload.facebook.com/ig-api-upload/{self.settings.meta_graph_version}/{container}"
        )
        self._rupload(upload_url, token, media, "Instagram video upload")
        return container

    def _carousel(self, ig_id: str, token: str, job: PostJob) -> str:
        children = []
        for media in job.media[:10]:
            if media.kind == "video":
                child = self._video_container(ig_id, token, media, "VIDEO", job, carousel_item=True)
            else:
                url = self.ensure_url(media, job.post_id, job.platform)
                child = self._post(f"{ig_id}/media", token, "Instagram carousel item",
                                   image_url=url, is_carousel_item="true")["id"]
            children.append(child)
        for child in children:
            self._wait(child, token)
        return self._post(f"{ig_id}/media", token, "Instagram carousel container",
                          media_type="CAROUSEL", children=",".join(children), caption=job.caption)["id"]

    def _wait(self, container: str, token: str) -> None:
        deadline = time.monotonic() + PROCESS_TIMEOUT
        while True:
            data = self._get(container, token, "Instagram processing status", fields="status_code,status")
            code = data.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"Instagram rejected the media: {data.get('status') or code}")
            if time.monotonic() > deadline:
                raise PublishError("Instagram is still processing the media after 15 minutes", transient=True)
            self.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------- Facebook


class FacebookPublisher(_MetaBase):
    def _creds(self) -> tuple[str, str]:
        s = self.settings
        if not s.fb_page_id or not s.fb_page_token:
            raise ConfigError("Facebook posting needs FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN")
        return s.fb_page_id, s.fb_page_token

    def publish(self, job: PostJob) -> PostResult:
        page, token = self._creds()
        if job.surface == "reel":
            return self._reel(page, token, job)
        if job.surface == "photos":
            return self._photos(page, token, job)
        raise PublishError(f"Facebook cannot post surface {job.surface!r}")

    def _reel(self, page: str, token: str, job: PostJob) -> PostResult:
        start = self._post(f"{page}/video_reels", token, "Facebook reel start", upload_phase="start")
        video_id = start["video_id"]
        upload_url = start.get("upload_url") or (
            f"https://rupload.facebook.com/video-upload/{self.settings.meta_graph_version}/{video_id}"
        )
        self._rupload(upload_url, token, job.media[0], "Facebook reel upload")
        self._post(f"{page}/video_reels", token, "Facebook reel publish", upload_phase="finish",
                   video_id=video_id, video_state="PUBLISHED", description=job.caption)

        notes = []
        deadline = time.monotonic() + PROCESS_TIMEOUT
        while True:
            status = self._get(video_id, token, "Facebook reel status", fields="status").get("status", {})
            video_status = status.get("video_status")
            phase = (status.get("publishing_phase") or {}).get("status")
            if video_status == "error" or phase == "error":
                raise PublishError(f"Facebook failed to process the reel: {status}")
            if video_status == "ready" and phase in (None, "complete"):
                break
            if time.monotonic() > deadline:
                notes.append("Facebook was still processing the reel when vauto stopped waiting")
                break
            self.sleep(POLL_SECONDS)

        url = None
        try:
            link = self._get(video_id, token, "Facebook permalink", fields="permalink_url").get("permalink_url")
            if link:
                url = link if link.startswith("http") else f"https://www.facebook.com{link}"
        except PublishError:
            pass
        self._first_comment(video_id, token, job, notes)
        return PostResult(job.platform, job.surface, "published", url=url, remote_id=video_id, notes=notes,
                          platform_post_id=video_id)

    def _photos(self, page: str, token: str, job: PostJob) -> PostResult:
        if len(job.media) == 1:
            with Path(job.media[0].path).open("rb") as fh:
                data = self._post(f"{page}/photos", token, "Facebook photo", files={"source": fh},
                                  caption=job.caption, published="true")
            post_id = data.get("post_id") or data.get("id")
            return PostResult(job.platform, job.surface, "published",
                              url=f"https://www.facebook.com/{post_id}", remote_id=post_id)

        attached: dict[str, str] = {}
        for i, media in enumerate(job.media[:10]):
            with Path(media.path).open("rb") as fh:
                photo = self._post(f"{page}/photos", token, "Facebook photo upload", files={"source": fh},
                                   published="false")
            attached[f"attached_media[{i}]"] = json.dumps({"media_fbid": photo["id"]})
        post = self._post(f"{page}/feed", token, "Facebook multi-photo post", message=job.caption, **attached)
        post_id = post.get("id")
        return PostResult(job.platform, job.surface, "published",
                          url=f"https://www.facebook.com/{post_id}", remote_id=post_id)


# ----------------------------------------------------------------------- Threads


class ThreadsPublisher(Publisher):
    """Threads API (graph.threads.net). Media is fetched from a public URL."""

    name = "threads"
    base = "https://graph.threads.net/v1.0"

    def _creds(self) -> tuple[str, str]:
        token = auth.threads_token(self.settings, self.session)
        if not self.settings.threads_user_id or not token:
            raise ConfigError("Threads direct posting needs THREADS_USER_ID and THREADS_ACCESS_TOKEN")
        return self.settings.threads_user_id, token

    def _post(self, path: str, token: str, context: str, **data: Any) -> dict[str, Any]:
        clean = {k: v for k, v in data.items() if v is not None}
        return call(self.session.post, f"{self.base}/{path}", data={**clean, "access_token": token},
                    timeout=120, context=context)

    def _item(self, user: str, token: str, media: MediaFile, job: PostJob, **extra: Any) -> str:
        url = self.ensure_url(media, job.post_id, job.platform)
        kind = "VIDEO" if media.kind == "video" else "IMAGE"
        key = "video_url" if kind == "VIDEO" else "image_url"
        return self._post(f"{user}/threads", token, "Threads container", media_type=kind, **{key: url}, **extra)["id"]

    def _wait(self, container: str, token: str) -> None:
        deadline = time.monotonic() + PROCESS_TIMEOUT
        while True:
            data = call(self.session.get, f"{self.base}/{container}",
                        params={"fields": "status,error_message", "access_token": token},
                        timeout=60, context="Threads status")
            status = data.get("status")
            if status in ("FINISHED", "PUBLISHED"):
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(f"Threads rejected the media: {data.get('error_message') or status}")
            if time.monotonic() > deadline:
                raise PublishError("Threads is still processing after 15 minutes", transient=True)
            self.sleep(POLL_SECONDS)

    def publish(self, job: PostJob) -> PostResult:
        user, token = self._creds()
        if len(job.media) == 1:
            container = self._item(user, token, job.media[0], job, text=job.caption)
        else:
            children = [self._item(user, token, m, job, is_carousel_item="true") for m in job.media[:10]]
            for child in children:
                self._wait(child, token)
            container = self._post(f"{user}/threads", token, "Threads carousel", media_type="CAROUSEL",
                                   children=",".join(children), text=job.caption)["id"]
        self._wait(container, token)
        post_id = self._post(f"{user}/threads_publish", token, "Threads publish", creation_id=container)["id"]
        url = None
        try:
            url = call(self.session.get, f"{self.base}/{post_id}",
                       params={"fields": "permalink", "access_token": token},
                       timeout=30, context="Threads permalink").get("permalink")
        except PublishError:
            pass
        return PostResult(job.platform, job.surface, "published", url=url, remote_id=post_id,
                          platform_post_id=post_id)
