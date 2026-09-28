"""Views, likes, comments and shares for the posts you made.

Each posting route that can report numbers has a reader here. Routes that
cannot (phone hand-off, browser automation, Telegram, TikTok drafts, China)
are skipped; you can type those numbers in yourself on the My posts tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import requests

from . import auth
from .config import Settings
from .errors import PublishError, VautoError
from .http import call
from .insights import instagram_metrics
from .models import PostJob, PostResult, label_for
from .scheduler import JobRow, iso, parse_iso, utcnow

METRICS = ("views", "reach", "likes", "comments", "shares", "saved")
BSKY_APPVIEW = "https://public.api.bsky.app"
THREADS_METRICS = ("views", "likes", "replies", "reposts", "quotes", "shares")
# Zernio reports impressions for platforms that have no "views" figure.
ZERNIO_ALIASES = {"saves": "saved", "impressions": "impressions"}


class NoStats(VautoError):
    """This route cannot report numbers."""


def _num(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        return float(sum(_num(v) for v in value.values()))
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _first_value(entry: dict[str, Any]) -> Any:
    if "total_value" in entry:
        return (entry.get("total_value") or {}).get("value")
    values = entry.get("values") or [{}]
    return values[0].get("value") if values else None


# ------------------------------------------------------------------ readers


def facebook_metrics(settings: Settings, job: PostJob, result: PostResult,
                     session: requests.Session) -> dict[str, float]:
    token = settings.fb_page_token
    if not token:
        raise PublishError("no Facebook Page token (FB_PAGE_ACCESS_TOKEN)")
    graph = f"https://{settings.meta_graph_host}/{settings.meta_graph_version}"
    if job.surface == "reel" and result.remote_id:
        data = call(session.get, f"{graph}/{result.remote_id}/video_insights", params={"access_token": token},
                    timeout=30, context="Facebook insights")
        out: dict[str, float] = {}
        for entry in data.get("data", []):
            name, value = entry.get("name"), _first_value(entry)
            if name in ("blue_reels_play_count", "fb_reels_total_plays", "total_video_views"):
                out["views"] = max(out.get("views", 0.0), _num(value))
            elif name in ("post_impressions_unique", "total_video_impressions_unique"):
                out["reach"] = _num(value)
            elif name in ("post_video_likes_by_reaction_type", "total_video_reactions_by_type_total"):
                out["likes"] = _num(value)
            elif name == "post_video_social_actions" and isinstance(value, dict):
                actions = {str(k).lower(): _num(v) for k, v in value.items()}
                out["comments"] = actions.get("comment", 0.0)
                out["shares"] = actions.get("share", 0.0)
        return out
    if not result.remote_id:
        raise NoStats("no Facebook post id was saved")
    data = call(session.get, f"{graph}/{result.remote_id}",
                params={"fields": "shares,reactions.limit(0).summary(true),comments.limit(0).summary(true)",
                        "access_token": token}, timeout=30, context="Facebook post stats")
    return {
        "likes": _num(((data.get("reactions") or {}).get("summary") or {}).get("total_count")),
        "comments": _num(((data.get("comments") or {}).get("summary") or {}).get("total_count")),
        "shares": _num((data.get("shares") or {}).get("count")),
    }


def threads_metrics(settings: Settings, media_id: str, session: requests.Session) -> dict[str, float]:
    token = auth.threads_token(settings, session)
    if not token:
        raise PublishError("no Threads token (THREADS_ACCESS_TOKEN)")
    data = call(session.get, f"https://graph.threads.net/v1.0/{media_id}/insights",
                params={"metric": ",".join(THREADS_METRICS), "access_token": token},
                timeout=30, context="Threads insights")
    raw = {e.get("name"): _num(_first_value(e)) for e in data.get("data", [])}
    return {"views": raw.get("views", 0.0), "likes": raw.get("likes", 0.0), "comments": raw.get("replies", 0.0),
            "shares": raw.get("reposts", 0.0) + raw.get("quotes", 0.0) + raw.get("shares", 0.0)}


def bluesky_metrics(uri: str, session: requests.Session) -> dict[str, float]:
    data = call(session.get, f"{BSKY_APPVIEW}/xrpc/app.bsky.feed.getPosts", params={"uris": uri},
                timeout=30, context="Bluesky post stats")
    posts = data.get("posts") or []
    if not posts:
        raise PublishError("Bluesky did not return the post (deleted?)")
    p = posts[0]
    return {"likes": _num(p.get("likeCount")), "comments": _num(p.get("replyCount")),
            "shares": _num(p.get("repostCount")) + _num(p.get("quoteCount"))}


def mastodon_metrics(settings: Settings, status_id: str, session: requests.Session) -> dict[str, float]:
    if not settings.mastodon_instance:
        raise PublishError("MASTODON_INSTANCE is not set")
    headers = {"Authorization": f"Bearer {settings.mastodon_access_token}"} if settings.mastodon_access_token else {}
    data = call(session.get, f"{settings.mastodon_instance}/api/v1/statuses/{status_id}", headers=headers,
                timeout=30, context="Mastodon post stats")
    return {"likes": _num(data.get("favourites_count")), "comments": _num(data.get("replies_count")),
            "shares": _num(data.get("reblogs_count"))}


def zernio_metrics(settings: Settings, zernio_post_id: str, platform: str,
                   session: requests.Session) -> dict[str, float]:
    """Per-post numbers from Zernio's analytics (needs Zernio's analytics add-on)."""
    from .publishers.zernio import ZernioPublisher

    pub = ZernioPublisher(settings, session=session)
    data = call(session.get, pub._url("analytics"), headers=pub.headers, params={"postId": zernio_post_id},
                timeout=30, context="Zernio analytics")
    name = pub.zernio_name(platform)
    entries = [e for e in data.get("platformAnalytics") or [] if isinstance(e, dict)]
    match = next((e for e in entries if e.get("platform") == name), None)
    if match is None and len(entries) == 1:
        match = entries[0]
    analytics = (match or {}).get("analytics") if match else data.get("analytics")
    if not isinstance(analytics, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in analytics.items():
        key = ZERNIO_ALIASES.get(key, key)
        if key in METRICS or key == "impressions":
            out[key] = _num(value)
    if not out.get("views") and out.get("impressions"):
        out["views"] = out["impressions"]
    out.pop("impressions", None)
    return out


# ---------------------------------------------------------------- dispatch


def _ig_token_available(settings: Settings) -> bool:
    return bool(settings.ig_access_token or auth.store_for(settings).get("instagram"))


def reader_for(settings: Settings, job: PostJob, result: PostResult | None) -> str | None:
    """Which reader can report numbers for this post, or None."""
    if result is None or job.surface in ("trial_report", "story"):
        return None
    if job.platform == "instagram" and (result.platform_post_id or job.backend == "meta") \
            and _ig_token_available(settings):
        return "instagram"
    if job.backend == "zernio" and result.remote_id and settings.zernio_api_key:
        return "zernio"
    if job.backend == "meta" and job.platform == "facebook" and result.remote_id:
        return "facebook"
    if job.backend == "threads" and (result.platform_post_id or result.remote_id):
        return "threads"
    if job.backend == "bluesky" and result.remote_id:
        return "bluesky"
    if job.backend == "mastodon" and result.remote_id:
        return "mastodon"
    return None


def fetch(settings: Settings, job: PostJob, result: PostResult, session: requests.Session) -> dict[str, float]:
    reader = reader_for(settings, job, result)
    if reader == "instagram":
        return instagram_metrics(settings, result.platform_post_id or result.remote_id or "", session)
    if reader == "zernio":
        return zernio_metrics(settings, result.remote_id or "", job.platform, session)
    if reader == "facebook":
        return facebook_metrics(settings, job, result, session)
    if reader == "threads":
        return threads_metrics(settings, result.platform_post_id or result.remote_id or "", session)
    if reader == "bluesky":
        return bluesky_metrics(result.remote_id or "", session)
    if reader == "mastodon":
        return mastodon_metrics(settings, result.remote_id or "", session)
    raise NoStats(f"{label_for(job.platform, job.surface)} does not report numbers to vauto")


# ----------------------------------------------------------------- refresh


@dataclass
class RefreshReport:
    updated: int = 0
    confirmed: int = 0  # scheduled posts that turned out to be live
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def text(self) -> str:
        parts = [f"updated numbers for {self.updated} post(s)"]
        if self.confirmed:
            parts.append(f"{self.confirmed} scheduled post(s) now live")
        if self.skipped:
            parts.append(f"{self.skipped} can't report numbers")
        return "; ".join(parts) + ("" if not self.errors else f"; {len(self.errors)} problem(s)")

    def to_dict(self) -> dict[str, Any]:
        return {"updated": self.updated, "confirmed": self.confirmed, "skipped": self.skipped,
                "errors": self.errors, "text": self.text()}


def _confirm_zernio(settings: Settings, row: JobRow, tracker, session: requests.Session) -> bool:
    """A post Zernio scheduled for us: ask Zernio whether it went out, and save its link."""
    from .publishers.zernio import ZernioPublisher

    entry = ZernioPublisher(settings, session=session).post_status(row.result.remote_id, row.job.platform)
    status = entry.get("status")
    if status == "published":
        url = next((entry.get(k) for k in ("publishedUrl", "platformPostUrl", "postUrl", "url") if entry.get(k)),
                   row.result.url)
        done = PostResult(row.job.platform, row.job.surface, "published", url=url, remote_id=row.result.remote_id,
                          run_at=row.run_at, platform_post_id=entry.get("platformPostId"),
                          notes=["confirmed live on Zernio"])
        tracker.store.finish(row.idem_key, done)
        row.status, row.result = "published", done
        return True
    if status in ("failed", "error"):
        error = entry.get("error") or entry.get("errorMessage") or "failed on Zernio"
        tracker.store.finish(row.idem_key, PostResult(row.job.platform, row.job.surface, "failed",
                                                      remote_id=row.result.remote_id, error=str(error)))
        row.status = "failed"
    return False


def refresh(settings: Settings, tracker, session: requests.Session | None = None, post_id: str | None = None,
            days: int = 30, now: datetime | None = None) -> RefreshReport:
    """Fetch fresh numbers for posts from the last ``days`` days (or one post)."""
    session = session or requests.Session()
    now = now or utcnow()
    since = None if post_id else now - timedelta(days=days)
    report = RefreshReport()
    for row in tracker.store.rows(limit=5000, post_id=post_id):
        if row.result is None or row.job.surface == "trial_report":
            continue
        if since and parse_iso(row.run_at) < since:
            continue
        if row.status in ("scheduled", "submitted") and row.job.backend == "zernio" and row.result.remote_id \
                and settings.zernio_api_key and parse_iso(row.run_at) <= now:
            try:
                report.confirmed += _confirm_zernio(settings, row, tracker, session)
            except (VautoError, requests.RequestException) as exc:
                report.errors.append(f"{row.job.label}: {exc}")
        if row.status != "published":
            continue
        if reader_for(settings, row.job, row.result) is None:
            report.skipped += 1
            continue
        try:
            metrics = fetch(settings, row.job, row.result, session)
        except (VautoError, requests.RequestException) as exc:
            report.errors.append(f"{row.job.label}: {exc}")
            continue
        except Exception as exc:  # a malformed answer from one platform must not stop the rest
            report.errors.append(f"{row.job.label}: {type(exc).__name__}: {exc}")
            continue
        if metrics:
            tracker.add_stats(row.idem_key, metrics, source=reader_for(settings, row.job, row.result) or "",
                              at=now)
            report.updated += 1
    tracker.set_meta("stats_refreshed_at", iso(now))
    return report
