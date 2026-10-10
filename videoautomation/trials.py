"""Trial Reel results from saved numbers: rates per view, your usual trial, and a verdict.

Instagram judges a Trial Reel over 72 hours on how strangers respond, so the useful
question is "did this do better than my usual trial?". Raw views alone mislead: shares
and saves per view say whether people valued it. Everything here reads only the numbers
vauto has stored (the worker refreshes them every few hours), so looking at results
never calls Instagram.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from .scheduler import JobStore, parse_iso, utcnow

WIN = 1.5  # this many times your usual trial counts as a winner
LOW = 0.5  # under half your usual views is a miss
MIN_HISTORY = 3  # trials with numbers needed before comparing
EARLY_HOURS = 24  # numbers before this are a first look, not a verdict
LIVE = ("published", "scheduled", "submitted")


@dataclass
class TrialResult:
    key: str
    post_id: str
    caption: str
    run_at: str
    status: str
    url: str | None
    metrics: dict[str, float] = field(default_factory=dict)
    rates: dict[str, float] = field(default_factory=dict)
    views_x: float | None = None  # views compared with your usual trial
    engage_x: float | None = None  # shares + saves per view compared with your usual trial
    verdict: str = "no_numbers"  # winner | average | below | early | no_history | no_numbers | waiting
    hours_live: float = 0.0
    standalone: bool = True
    main_metrics: dict[str, float] = field(default_factory=dict)  # the main Reel's numbers, when paired

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rates(metrics: dict[str, float]) -> dict[str, float]:
    """Per-view rates: shares and saves per 1,000 views, likes per 100 views."""
    views = metrics.get("views") or 0
    if views <= 0:
        return {}
    out = {
        "shares_per_1k": metrics.get("shares", 0) / views * 1000,
        "saves_per_1k": metrics.get("saved", 0) / views * 1000,
        "likes_per_100": metrics.get("likes", 0) / views * 100,
        "comments_per_1k": metrics.get("comments", 0) / views * 1000,
    }
    out["engage_per_1k"] = out["shares_per_1k"] + out["saves_per_1k"]
    return out


def collect(store: JobStore, limit: int = 60, now: datetime | None = None) -> list[TrialResult]:
    """Your recent Trial Reels with their latest saved numbers, newest first, judged
    against your usual trial."""
    from .tracker import Tracker

    now = now or utcnow()
    tracker = Tracker(store)
    rows = store.conn.execute(
        "SELECT * FROM jobs WHERE platform = 'instagram' AND surface = 'trial_reel'"
        " AND status NOT IN ('failed', 'skipped', 'dry_run') ORDER BY run_at DESC LIMIT ?", (limit,)
    ).fetchall()
    from .scheduler import JobRow

    jobs = [JobRow(r) for r in rows]
    latest = tracker.latest_stats([j.idem_key for j in jobs] + [j.job.depends_on for j in jobs if j.job.depends_on])
    results = []
    for j in jobs:
        stats = latest.get(j.idem_key)
        metrics = dict(stats["metrics"]) if stats else {}
        run_at = parse_iso(j.run_at)
        upcoming = j.status not in LIVE or run_at > now
        results.append(TrialResult(
            key=j.idem_key, post_id=j.job.post_id, caption=(j.job.caption or "").strip().split("\n", 1)[0][:90],
            run_at=j.run_at, status=j.status, url=j.result.url if j.result else None, metrics=metrics,
            rates=rates(metrics), hours_live=max(0.0, (now - run_at).total_seconds() / 3600),
            standalone=not j.job.depends_on,
            verdict="waiting" if upcoming else "no_numbers",
            main_metrics=dict((latest.get(j.job.depends_on) or {}).get("metrics", {})) if j.job.depends_on else {},
        ))
    for r in results:
        judge(r, baseline(results, exclude=r.key))
    return results


def baseline(results: list[TrialResult], exclude: str | None = None) -> dict[str, float] | None:
    """Your usual trial: the median views and shares+saves per 1,000 views of trials that have
    numbers after a day (None until there are MIN_HISTORY of them)."""
    pool = [r for r in results if r.key != exclude and r.metrics.get("views") and r.hours_live >= EARLY_HOURS]
    if len(pool) < MIN_HISTORY:
        return None
    return {"views": median(r.metrics["views"] for r in pool),
            "engage_per_1k": median(r.rates.get("engage_per_1k", 0) for r in pool),
            "trials": len(pool)}


def judge(result: TrialResult, base: dict[str, float] | None) -> TrialResult:
    """Set views_x, engage_x and the verdict (in place). Also returns the result."""
    if result.status not in LIVE or result.verdict == "waiting":
        result.verdict = "waiting"
        return result
    views = result.metrics.get("views") or 0
    if not views:
        result.verdict = "no_numbers"
        return result
    if base is None:
        result.verdict = "no_history"
        return result
    result.views_x = views / base["views"] if base["views"] else None
    engage = result.rates.get("engage_per_1k", 0)
    result.engage_x = engage / base["engage_per_1k"] if base["engage_per_1k"] else None
    vx, ex = result.views_x or 0, result.engage_x or 0
    if result.hours_live < EARLY_HOURS:
        result.verdict = "early"
    elif vx >= WIN or (ex >= WIN and vx >= 0.75):
        result.verdict = "winner"
    elif vx < LOW and ex < 1:
        result.verdict = "below"
    else:
        result.verdict = "average"
    return result


def compact(n: float) -> str:
    for unit, size in (("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.1f}".rstrip("0").rstrip(".") + unit
    return f"{n:.0f}"


def describe(result: TrialResult, base: dict[str, float] | None = None) -> str:
    """One plain line about a trial, e.g. '12.4K views, 2.3x your usual; 3.1 shares+saves per 1K (usual 1.2)'."""
    views = result.metrics.get("views")
    if not views:
        return {"waiting": "not out yet", "no_numbers": "no numbers yet"}.get(result.verdict, "no numbers yet")
    parts = [f"{compact(views)} views"]
    if result.views_x is not None:
        parts[0] += f", {result.views_x:.1f}x your usual"
    engage = result.rates.get("engage_per_1k")
    if engage is not None:
        usual = f" (usual {base['engage_per_1k']:.1f})" if base else ""
        parts.append(f"{engage:.1f} shares+saves per 1K{usual}")
    return "; ".join(parts)


def verdict_lines(result: TrialResult, base: dict[str, float] | None, public_url: str | None) -> list[str]:
    """What to do with a judged trial, in plain words."""
    lines = [describe(result, base)]
    if result.verdict == "winner":
        lines.append("Winner: share it with your followers (Instagram: Profile → Reels → Trials → Share)"
                     + (f" {result.url}" if result.url else ""))
        if public_url:
            lines.append(f"Or post it as a fresh main Reel: {public_url}/?reuse={result.post_id}")
    elif result.verdict == "below":
        lines.append("Below your usual trial: leave it; the next version tests a different hook")
    elif result.verdict == "early":
        lines.append("First day only: the verdict comes after 24 hours")
    elif result.verdict == "no_history":
        lines.append(f"Not enough past trials to compare yet (needs {MIN_HISTORY} with numbers)")
    return lines


def recent(store: JobStore, days: int = 4, now: datetime | None = None) -> tuple[list[TrialResult], dict | None]:
    """Trials that went out in the last ``days``, and your usual trial."""
    now = now or utcnow()
    results = collect(store, now=now)
    cutoff = now - timedelta(days=days)
    return [r for r in results if parse_iso(r.run_at) >= cutoff and parse_iso(r.run_at) <= now], baseline(results)
