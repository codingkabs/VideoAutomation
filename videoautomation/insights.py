"""Trial Reel results: compare the main Reel with its trial version.

Metrics come from Instagram's own insights API when an Instagram token is set
(direct Meta or not), because that works for posts made through Zernio too.
Without it, vauto asks Zernio's analytics (included with every Zernio account).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from . import auth, notify
from .config import Settings
from .errors import PublishError
from .http import call
from .models import PostJob, PostResult
from .publishers.base import Publisher
from .scheduler import JobStore

IG_METRICS = ("views", "reach", "likes", "comments", "shares", "saved", "total_interactions")
SHOWN = ("views", "reach", "likes", "comments", "shares", "saved")


@dataclass
class TrialComparison:
    post_id: str
    main_url: str | None
    trial_url: str | None
    main: dict[str, float] = field(default_factory=dict)
    trial: dict[str, float] = field(default_factory=dict)
    winner: str | None = None
    notes: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        if not self.main and not self.trial:
            return self.notes or ["no metrics available yet"]
        out = [f"{'metric':<10} {'main':>9} {'trial':>9}"]
        for key in SHOWN:
            if key in self.main or key in self.trial:
                out.append(f"{key:<10} {self.main.get(key, 0):>9.0f} {self.trial.get(key, 0):>9.0f}")
        if self.winner:
            out.append(f"winner: {self.winner} reel")
            if self.winner == "trial":
                out.append("tip: the trial did better; share it with followers from the Instagram app")
        return out + self.notes

    def to_dict(self) -> dict[str, Any]:
        return {"post_id": self.post_id, "main_url": self.main_url, "trial_url": self.trial_url,
                "main": self.main, "trial": self.trial, "winner": self.winner, "notes": self.notes,
                "lines": self.lines()}


def _value(entry: dict[str, Any]) -> float:
    if "total_value" in entry:
        return float((entry.get("total_value") or {}).get("value") or 0)
    values = entry.get("values") or [{}]
    return float(values[0].get("value") or 0)


def instagram_metrics(settings: Settings, media_id: str, session: requests.Session) -> dict[str, float]:
    token = auth.instagram_token(settings, session)
    if not token:
        raise PublishError("no Instagram token for insights")
    url = f"https://{settings.meta_graph_host}/{settings.meta_graph_version}/{media_id}/insights"
    try:
        data = call(session.get, url, params={"metric": ",".join(IG_METRICS), "access_token": token},
                    timeout=30, context="Instagram insights")
        entries = data.get("data", [])
    except PublishError:
        entries = []  # one unsupported metric fails the batch; ask one at a time
        for metric in IG_METRICS:
            try:
                entries += call(session.get, url, params={"metric": metric, "access_token": token},
                                timeout=30, context="Instagram insights").get("data", [])
            except PublishError:
                continue
    return {e["name"]: _value(e) for e in entries if "name" in e}


def zernio_metrics(settings: Settings, zernio_post_id: str, session: requests.Session) -> dict[str, float]:
    """Per-post metrics from Zernio's post timeline (shape varies; parsed defensively)."""
    from .publishers.zernio import ZernioPublisher

    pub = ZernioPublisher(settings, session=session)
    params = {"profileId": settings.zernio_profile_id} if settings.zernio_profile_id else {}
    data = call(session.get, pub._url("analytics/post-timeline"), headers=pub.headers, params=params,
                timeout=30, context="Zernio analytics")
    rows = data.get("posts") or data.get("data") or data.get("timeline") or []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and zernio_post_id in (row.get("postId"), row.get("_id"), row.get("id")):
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else row
            aliases = {"impressions": "views", "saves": "saved"}
            out = {}
            for key, value in metrics.items():
                name = aliases.get(key, key)
                if name in IG_METRICS and isinstance(value, (int, float)):
                    out[name] = float(value)
            return out
    return {}


def metrics_for(settings: Settings, backend: str, result: PostResult, session: requests.Session) -> dict[str, float]:
    media_id = result.platform_post_id or (result.remote_id if backend == "meta" else None)
    if not media_id and backend == "zernio" and result.remote_id:
        from .publishers.zernio import ZernioPublisher

        entry = ZernioPublisher(settings, session=session).post_status(result.remote_id, "instagram")
        media_id = entry.get("platformPostId")
        result.url = result.url or entry.get("publishedUrl")
    if media_id and (settings.ig_access_token or auth.store_for(settings).get("instagram")):
        return instagram_metrics(settings, media_id, session)
    if backend == "zernio" and result.remote_id and settings.zernio_api_key:
        return zernio_metrics(settings, result.remote_id, session)
    raise PublishError("no way to read Instagram metrics: set IG_ACCESS_TOKEN (or connect Zernio analytics)")


def _score(m: dict[str, float]) -> float:
    return m.get("views", 0) + 5 * (m.get("shares", 0) + m.get("saved", 0)) + 2 * m.get("comments", 0) + m.get("likes", 0)


def compare(settings: Settings, store: JobStore, trial_key: str,
            session: requests.Session | None = None) -> TrialComparison:
    session = session or requests.Session()
    trial_row = store.get(trial_key)
    if trial_row is None:
        raise PublishError("trial reel not found")
    main_row = store.get(trial_row.job.depends_on) if trial_row.job.depends_on else None
    comparison = TrialComparison(trial_row.job.post_id,
                                 main_row.result.url if main_row and main_row.result else None,
                                 trial_row.result.url if trial_row.result else None)
    for side, row in (("main", main_row), ("trial", trial_row)):
        if row is None or row.result is None or row.status not in ("published", "submitted", "scheduled"):
            comparison.notes.append(f"{side} reel has not published")
            continue
        try:
            setattr(comparison, side, metrics_for(settings, row.job.backend, row.result, session))
        except PublishError as exc:
            comparison.notes.append(f"{side}: {exc}")
    if comparison.main and comparison.trial:
        comparison.winner = "trial" if _score(comparison.trial) > _score(comparison.main) else "main"
    return comparison


def trial_keys(store: JobStore, limit: int = 20) -> list[str]:
    return [row.idem_key for row in store.rows(limit=500) if row.job.surface == "trial_reel"][:limit]


class InsightsPublisher(Publisher):
    """Runs the scheduled 'trial report' job: compares and messages you the result."""

    name = "insights"

    def publish(self, job: PostJob) -> PostResult:
        store = JobStore(self.settings.db_path)
        comparison = compare(self.settings, store, job.options["trial_key"], self.session)
        text = "\n".join(["📊 Trial Reel results", comparison.main_url or "", *comparison.lines()])
        notify.send(self.settings, text, self.session)
        return PostResult(job.platform, job.surface, "reported", notes=comparison.lines())
