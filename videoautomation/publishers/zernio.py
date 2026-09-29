"""Zernio unified API publisher.

Covers the platforms whose own APIs are gated (TikTok and YouTube audits,
Snapchat allowlist). Can also post Instagram and Facebook, including Trial
Reels. Future ``run_at`` values become Zernio-side ``scheduledFor``, so no
local worker is needed for those jobs.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from ..config import platform_spec
from ..errors import ConfigError, PublishError
from ..models import PostJob, PostResult
from ..scheduler import iso, parse_iso, utcnow
from .base import Publisher, call

POLL_SECONDS = 10.0
POLL_TIMEOUT = 10 * 60
URL_KEYS = ("publishedUrl", "platformPostUrl", "postUrl", "url")
DONE = {"published", "failed", "error"}


def _post_obj(data: dict[str, Any]) -> dict[str, Any]:
    post = data.get("post")
    return post if isinstance(post, dict) else data


def _platform_entry(post: dict[str, Any], platform: str) -> dict[str, Any]:
    for entry in post.get("platforms") or []:
        if isinstance(entry, dict) and entry.get("platform") == platform:
            return entry
    return {}


class ZernioPublisher(Publisher):
    name = "zernio"
    remote_schedule = True

    @property
    def headers(self) -> dict[str, str]:
        if not self.settings.zernio_api_key:
            raise ConfigError("ZERNIO_API_KEY is not set")
        return {"Authorization": f"Bearer {self.settings.zernio_api_key}"}

    def _url(self, path: str) -> str:
        return f"{self.settings.zernio_base_url}/{path.lstrip('/')}"

    def account_id(self, platform: str) -> str:
        if not self.settings.zernio_api_key:
            raise ConfigError(f"{platform} posts through Zernio: set ZERNIO_API_KEY (see README)")
        account = self.settings.zernio_accounts.get(platform)
        if not account:
            raise ConfigError(
                f"No Zernio account for {platform}. Set ZERNIO_ACCOUNT_{platform.upper()} "
                "(run `vauto accounts` to list your connected accounts)."
            )
        return account

    # ------------------------------------------------------------- payloads
    def platform_data(self, job: PostJob) -> dict[str, Any]:
        o, s = job.options, self.settings
        p = job.platform
        if p == "instagram":
            data: dict[str, Any] = {}
            if job.surface == "story":
                data["contentType"] = "story"
            if o.get("trial_graduation"):
                data["trialParams"] = {"graduationStrategy": o["trial_graduation"]}
            if o.get("thumb_offset_ms") is not None:
                data["thumbOffset"] = int(o["thumb_offset_ms"])
            if o.get("audio_name"):
                data["audioName"] = o["audio_name"]
            return data
        if p == "facebook":
            return {"contentType": "story"} if job.surface == "story" else {}
        if p == "tiktok":
            data = {
                "privacyLevel": o.get("privacy_level") or s.tiktok_privacy_level,
                "allowComment": True,
                "contentPreviewConfirmed": True,
                "expressConsentGiven": True,
                "draft": bool(o.get("draft")),
                "commercialContentType": "none",
            }
            if job.surface == "photos":
                data["autoAddMusic"] = bool(o.get("auto_add_music", True))
                data["photoCoverIndex"] = 0
                if o.get("description"):
                    data["description"] = o["description"]
            else:
                data["allowDuet"] = True
                data["allowStitch"] = True
                if o.get("thumb_offset_ms") is not None:
                    data["videoCoverTimestampMs"] = int(o["thumb_offset_ms"])
            return data
        if p == "youtube":
            return {"title": o.get("title") or "New Short", "visibility": o.get("visibility") or s.youtube_visibility}
        if p == "snapchat":
            return {"contentType": o.get("content_type") or s.snapchat_content_type}
        if p == "pinterest":
            data = {"title": o.get("title") or job.caption[:100]}
            if o.get("board_id") or s.pinterest_board_id:
                data["boardId"] = o.get("board_id") or s.pinterest_board_id
            if o.get("link") or s.pinterest_link:
                data["link"] = o.get("link") or s.pinterest_link
            if job.surface == "video_pin" and o.get("thumb_offset_ms") is not None:
                data["coverImageKeyFrameTime"] = int(o["thumb_offset_ms"]) // 1000
            return data
        if p == "reddit":
            data = {"title": o.get("title") or job.caption[:300]}
            subreddit = o.get("subreddit") or s.reddit_subreddit
            if subreddit:
                data["subreddit"] = subreddit
            if o.get("flair_id") or s.reddit_flair_id:
                data["flairId"] = o.get("flair_id") or s.reddit_flair_id
            if job.surface == "video":
                data["nativeVideo"] = True
            return data
        return {}

    @staticmethod
    def zernio_name(platform: str) -> str:
        return platform_spec(platform).get("zernio_platform") or platform

    def build_body(self, job: PostJob) -> dict[str, Any]:
        body: dict[str, Any] = {
            "content": job.caption,
            "platforms": [{
                "platform": self.zernio_name(job.platform),
                "accountId": self.account_id(job.platform),
                "platformSpecificData": self.platform_data(job),
            }],
            "mediaItems": [{"type": m.kind, "url": m.url} for m in job.media],
        }
        if job.options.get("tags"):
            body["tags"] = list(job.options["tags"])
        if job.run_at and parse_iso(job.run_at) > utcnow() + timedelta(minutes=1):
            body["scheduledFor"] = iso(parse_iso(job.run_at))
        else:
            body["publishNow"] = True
        return body

    # ------------------------------------------------------------- publish
    def check_tiktok(self, job: PostJob) -> list[str]:
        """TikTok requires creator info before posting. Best effort: warn, don't block."""
        try:
            info = call(self.session.get, self._url(f"accounts/{self.account_id('tiktok')}/tiktok/creator-info"),
                        headers=self.headers, timeout=30, context="TikTok creator info")
        except PublishError as exc:
            return [f"could not read TikTok creator info ({exc})"]
        info = info.get("data", info) if isinstance(info.get("data"), dict) else info
        notes = []
        options = info.get("privacyLevelOptions") or info.get("privacy_level_options")
        level = job.options.get("privacy_level") or self.settings.tiktok_privacy_level
        if isinstance(options, list) and options and level not in options:
            raise PublishError(f"TikTok privacy level {level} is not allowed for this account; options: {options}")
        max_s = info.get("maxVideoPostDurationSec") or info.get("max_video_post_duration_sec")
        duration = job.options.get("duration_s")
        if max_s and duration and duration > float(max_s):
            raise PublishError(f"TikTok allows this account {max_s}s videos; this one is {duration:.0f}s")
        return notes

    def publish(self, job: PostJob) -> PostResult:
        self.account_id(job.platform)  # fail on missing setup before uploading anything
        notes = self.check_tiktok(job) if job.platform == "tiktok" else []
        self.ensure_urls(job)
        body = self.build_body(job)
        data = call(self.session.post, self._url("posts"), headers=self.headers, json=body, timeout=120,
                    context=f"Zernio {job.platform} post")
        post = _post_obj(data)
        post_id = post.get("_id") or post.get("id")

        if "scheduledFor" in body:
            return PostResult(job.platform, job.surface, "scheduled", remote_id=post_id,
                              run_at=body["scheduledFor"], notes=notes + ["scheduled on Zernio"])
        return self._await(job, post_id, post, notes)

    def _await(self, job: PostJob, post_id: str | None, post: dict[str, Any], notes: list[str]) -> PostResult:
        deadline = time.monotonic() + POLL_TIMEOUT
        name = self.zernio_name(job.platform)
        entry = _platform_entry(post, name)
        if not post_id and (entry.get("status") or post.get("status")) not in DONE:
            return PostResult(job.platform, job.surface, "submitted",
                              notes=notes + ["Zernio accepted the post but returned no id; check the Zernio dashboard"])
        while post_id and (entry.get("status") or post.get("status")) not in DONE:
            if time.monotonic() > deadline:
                return PostResult(job.platform, job.surface, "submitted", remote_id=post_id,
                                  notes=notes + ["Zernio is still publishing; check the Zernio dashboard"])
            self.sleep(POLL_SECONDS)
            post = _post_obj(call(self.session.get, self._url(f"posts/{post_id}"), headers=self.headers,
                                  timeout=60, context="Zernio post status"))
            entry = _platform_entry(post, name)

        status = entry.get("status") or post.get("status")
        if status in ("failed", "error"):
            error = entry.get("error") or entry.get("errorMessage") or post.get("error") or "failed on Zernio"
            raise PublishError(f"{job.platform} via Zernio: {error}")
        url = next((entry.get(k) for k in URL_KEYS if entry.get(k)), None)
        final = "draft" if job.options.get("draft") else "published"
        if final == "draft" and job.platform == "youtube":
            notes = notes + ["uploaded as Private: in the YouTube app, open it and set it to Public to post it"]
        elif final == "draft":
            notes = notes + ["sent to your TikTok inbox: open TikTok, add a sound, and post"]
        return PostResult(job.platform, job.surface, final, url=url, remote_id=post_id, notes=notes,
                          platform_post_id=entry.get("platformPostId"))

    def post_status(self, post_id: str, platform: str) -> dict[str, Any]:
        """Latest Zernio view of one platform entry (status, URL, platform post id)."""
        post = _post_obj(call(self.session.get, self._url(f"posts/{post_id}"), headers=self.headers,
                              timeout=60, context="Zernio post status"))
        return _platform_entry(post, self.zernio_name(platform)) or {"status": post.get("status")}

    def best_times(self, platform: str) -> list[tuple[int, int]]:
        """(weekday 0=Mon, hour) pairs Zernio recommends, best first. Needs ZERNIO_PROFILE_ID
        and a few posts of history; returns [] when unavailable."""
        if not self.settings.zernio_profile_id:
            return []
        try:
            data = call(self.session.get, self._url("analytics/best-time"), headers=self.headers,
                        params={"profileId": self.settings.zernio_profile_id, "platform": self.zernio_name(platform)},
                        timeout=30, context="Zernio best time")
        except PublishError:
            return []
        rows = data.get("bestTimes") or data.get("data") or data.get("slots") or []
        days = {d: i for i, d in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
        out: list[tuple[float, int, int]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            day = row.get("dayOfWeek", row.get("day"))
            hour = row.get("hour")
            if isinstance(day, str):
                day = days.get(day[:3].lower())
            if isinstance(day, int) and isinstance(hour, int) and 0 <= day <= 6 and 0 <= hour <= 23:
                out.append((float(row.get("score") or row.get("engagement") or 0), day, hour))
        out.sort(reverse=True)
        return [(d, h) for _, d, h in out]

    def list_accounts(self) -> list[dict[str, Any]]:
        data = call(self.session.get, self._url("accounts"), headers=self.headers, timeout=30,
                    context="Zernio accounts")
        accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else data.get("data", [])
        return accounts if isinstance(accounts, list) else []
