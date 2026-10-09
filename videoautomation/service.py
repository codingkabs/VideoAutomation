"""Operations shared by the CLI, the Telegram bot and the web app."""

from __future__ import annotations

import threading
import time as clock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import notify, timing
from .config import Settings
from .errors import ConfigError, VautoError
from .media.variants import VariantOptions
from .models import PostJob, PostResult
from .pipeline import PostPlan, PostRequest, build_plan, execute
from .publishers import make_publisher
from .scheduler import JobStore, iso, parse_iso, run_job, utcnow
from .storage import make_storage

SLOT_SETTINGS = {"main": ("main_slots", "VAUTO_MAIN_SLOTS"), "trial": ("trial_slots", "VAUTO_TRIAL_SLOTS")}
SLOT_HOLD_SECONDS = 1800  # a slot just handed out stays taken while its post renders
_held_slots: dict[str, float] = {}  # ISO time -> when the hold ends
_slot_lock = threading.Lock()


def slot_time(settings: Settings, kind: str, after: datetime | None = None, now: datetime | None = None,
              reserve: bool = False) -> datetime:
    """Next free main or trial posting slot (after ``after`` when given). ``reserve`` holds it
    for a while, so two posts sent a moment apart don't both take it before either is queued."""
    if kind not in SLOT_SETTINGS:
        raise ConfigError(f"Unknown slot {kind!r}; use slot:main or slot:trial")
    attr, name = SLOT_SETTINGS[kind]
    now = now or utcnow()
    start = max(now, after) if after else now
    with _slot_lock:
        expired = clock.time()
        for key in [k for k, until in _held_slots.items() if until < expired]:
            del _held_slots[key]
        since = iso(now - timedelta(days=1))
        taken = [parse_iso(r.run_at) for r in JobStore(settings.db_path).reel_rows(since)]
        taken += [parse_iso(t) for t in _held_slots]
        when = timing.free_slot(timing.parse_slots(getattr(settings, attr), name), start,
                                timing.zone(settings), taken, name)
        if reserve:
            _held_slots[iso(when)] = clock.time() + SLOT_HOLD_SECONDS
    return when


def day_slots(settings: Settings, day: date) -> list[dict[str, Any]]:
    """The main and trial slots of ``day`` (local), each with the Instagram Reel that holds it."""
    tz = timing.zone(settings)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    rows = JobStore(settings.db_path).reel_rows(iso(start - timing.SLOT_GAP)) if settings.db_path.is_file() else []
    out = []
    for kind, (attr, name) in SLOT_SETTINGS.items():
        for t in timing.parse_slots(getattr(settings, attr), name).get(day.weekday(), []):
            when = datetime.combine(day, t, tzinfo=tz).astimezone(timezone.utc)
            holder = next((r for r in rows if abs(parse_iso(r.run_at) - when) < timing.SLOT_GAP), None)
            out.append({
                "kind": kind, "time": iso(when), "local": t.strftime("%H:%M"),
                "taken_by": None if holder is None else {
                    "id": holder.idem_key[:10], "post_id": holder.job.post_id, "surface": holder.job.surface,
                    "status": holder.status, "run_at": holder.run_at,
                    "url": holder.result.url if holder.result else None},
            })
    return sorted(out, key=lambda s: s["time"])


def resolve_publish_at(settings: Settings, at: str | None, platforms: list[str],
                       now: datetime | None = None, after: str | None = None,
                       reserve: bool = False) -> tuple[str | None, str | None]:
    """Turn an --at value into (ISO UTC time or None for now, human note).
    ``after`` (ISO) makes a slot come after that time; ``reserve`` holds a slot (real posts only)."""
    if at and at.strip().lower().startswith("slot:"):
        kind = at.strip().lower().split(":", 1)[1]
        when = slot_time(settings, kind, parse_iso(after) if after else None, now, reserve)
        local = when.astimezone(timing.zone(settings)).strftime("%a %d %b %H:%M %Z")
        return iso(when), f"{kind} slot: {local}"
    if at and at.strip().lower() == "best":
        hours: list[tuple[int, int]] = []
        zernio_platforms = [p for p in platforms if settings.backends.get(p) == "zernio"]
        if settings.zernio_api_key and settings.zernio_profile_id and zernio_platforms:
            from .publishers.zernio import ZernioPublisher

            hours = ZernioPublisher(settings).best_times(zernio_platforms[0])
        when, source = timing.best_time(settings, now, zernio_platforms[0] if zernio_platforms else "instagram",
                                        hours)
        local = when.astimezone(timing.zone(settings)).strftime("%a %d %b %H:%M %Z")
        return iso(when), f"best time: {local} (from {source})"
    when = timing.parse_when(at, settings, now)
    if when is None:
        return None, None
    local = when.astimezone(timing.zone(settings)).strftime("%a %d %b %H:%M %Z")
    return iso(when), f"scheduled for {local}"


def resolve_times(settings: Settings, at: str | None, trial_at: str | None, platforms: list[str],
                  dry_run: bool = False) -> tuple[str | None, str | None, list[str]]:
    """Resolve the post time and the Trial Reel time (which comes after the post).
    Returns (publish_at, trial_at, notes); real posts hold the slots they get."""
    publish_at, note = resolve_publish_at(settings, at, platforms, reserve=not dry_run)
    notes = [note] if note else []
    trial_iso = None
    if trial_at:
        trial_iso, _ = resolve_publish_at(settings, trial_at, ["instagram"], after=publish_at, reserve=not dry_run)
        if trial_iso:
            local = parse_iso(trial_iso).astimezone(timing.zone(settings)).strftime("%a %d %b %H:%M %Z")
            notes.append(f"Trial Reel at {local}")
    return publish_at, trial_iso, notes


def make_request(settings: Settings, inputs: list[Path], caption: str, platforms: list[str] | None = None,
                 **opts: Any) -> PostRequest:
    """Build a PostRequest with the user's defaults filled in."""
    platforms = [p.strip().lower() for p in (platforms or settings.default_platforms) if p.strip()]
    unknown = [p for p in platforms if p not in settings.backends]
    if unknown:
        raise ConfigError(f"No posting route for: {', '.join(unknown)}. See `vauto platforms`.")
    variant = opts.pop("variant", None) or VariantOptions(font_path=settings.font_path)
    if opts.pop("trial_preset", None) == "rotate":
        variant.apply_preset(datetime.now(timing.zone(settings)).date())
    if opts.get("trial_only"):
        opts["trial"] = True
    if opts.get("trial") is None:
        opts["trial"] = settings.trial_default
    if opts.get("drafts") is None:
        opts["drafts"] = settings.drafts_default
    if opts.get("draft_platforms") is None:
        opts["draft_platforms"] = list(settings.draft_platforms)
    if not opts.get("first_comment_mode"):
        opts["first_comment_mode"] = settings.first_comment_mode
    if opts["first_comment_mode"] == "mine" and opts.get("first_comment") is None:
        opts["first_comment"] = settings.first_comment_text
    if opts.get("trial_delay") is None:
        opts["trial_delay"] = settings.trial_delay_minutes
    if not opts.get("trial_graduation"):
        opts["trial_graduation"] = settings.trial_graduation
    req = PostRequest(inputs=[Path(p) for p in inputs], caption=caption, platforms=platforms,
                      variant=variant, **{k: v for k, v in opts.items() if v is not None})
    if req.trial:
        req.variant.validate()
    return req


def post(settings: Settings, req: PostRequest) -> tuple[PostPlan, list[PostResult]]:
    plan = build_plan(req, settings)
    return plan, execute(plan, settings, req)


def publisher_factory(settings: Settings):
    storage = make_storage(settings)
    cache: dict[tuple[str, str], Any] = {}

    def publisher_for(job: PostJob):
        key = (job.backend, job.platform)
        if key not in cache:
            cache[key] = make_publisher(job.backend, job.platform, settings, storage)
        return cache[key]

    return publisher_for


def cancel(settings: Settings, key_prefix: str) -> PostResult:
    store = JobStore(settings.db_path)
    row = find(store, key_prefix)
    if row.status not in ("pending", "held"):
        raise VautoError(f"Only queued jobs and drafts can be cancelled; this one is {row.status}")
    reason = "draft discarded by you" if row.status == "held" else "cancelled by you"
    result = PostResult(row.job.platform, row.job.surface, "skipped", error=reason)
    store.finish(row.idem_key, result)
    return result


def cancel_post(settings: Settings, post_id: str) -> list[PostResult]:
    """Cancel every job of a post still waiting in vauto's queue (its Trial Reel, results check, ...)."""
    store = JobStore(settings.db_path)
    rows = [r for r in store.rows(post_id=post_id, limit=1000) if r.status == "pending"]
    if not rows:
        raise VautoError("Nothing of this post is waiting in the queue (it may have gone out already)")
    results = []
    for row in rows:
        result = PostResult(row.job.platform, row.job.surface, "skipped", error="cancelled by you")
        store.finish(row.idem_key, result)
        results.append(result)
    return results


def retry(settings: Settings, key_prefix: str) -> PostResult:
    store = JobStore(settings.db_path)
    row = find(store, key_prefix)
    if row.status not in ("failed", "skipped", "pending", "held"):
        raise VautoError(f"Only failed, skipped, queued or draft jobs can be posted again; this one is {row.status}")
    job = row.job
    if job.depends_on:
        parent = store.get(job.depends_on)
        if parent is not None and parent.status == "held":
            raise VautoError("Post the main Reel first; its Trial Reel follows it")
    job.options.pop("hold", None)
    job.run_at = iso(utcnow())
    store.replace_pending(job)
    if store.get(job.idem_key) is None:
        store.insert(job, status="running")
    return run_job(store, job, publisher_factory(settings))


def publish_drafts(settings: Settings, post_id: str) -> list[PostResult]:
    """Post a post's drafts that vauto is holding. Main posts go out now; a Trial Reel is
    scheduled 1-2 hours later and its results check after that (the worker runs both)."""
    from concurrent.futures import ThreadPoolExecutor

    store = JobStore(settings.db_path)
    rows = [r for r in store.rows(post_id=post_id, limit=1000) if r.status == "held"]
    if not rows:
        raise VautoError("This post has no drafts waiting")
    now = utcnow()
    mains = [r.job for r in rows if not r.job.depends_on]
    followers = [r.job for r in rows if r.job.depends_on]
    for job in mains:
        job.options.pop("hold", None)
        job.run_at = iso(now)
        store.requeue(job, "running")
    publisher_for = publisher_factory(settings)
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(mains)))) as pool:
        results = list(pool.map(lambda j: run_job(store, j, publisher_for), mains))

    return results + _schedule_followers(settings, store, followers, now)


def _schedule_followers(settings: Settings, store: JobStore, followers: list[PostJob],
                        now: datetime) -> list[PostResult]:
    """Queue held follow-ups: a Trial Reel 1-2 hours from now, then its results check."""
    import random
    from datetime import timedelta

    lo, hi = settings.trial_delay_minutes
    followed_at: dict[str, datetime] = {}
    results = []
    for job in sorted(followers, key=lambda j: j.surface == "trial_report"):
        job.options.pop("hold", None)
        if job.surface == "trial_report":
            when = followed_at.get(job.depends_on or "", now) + timedelta(hours=settings.trial_report_hours)
        else:
            when = now + timedelta(minutes=random.randint(lo, hi))
            followed_at[job.idem_key] = when
        job.run_at = iso(when)
        store.requeue(job, "pending")
        if job.surface != "trial_report":
            results.append(PostResult(job.platform, job.surface, "queued", run_at=job.run_at,
                                      notes=["posts after the main Reel; the worker sends it"]))
    return results


def release_followers(settings: Settings, parent_key: str) -> list[PostResult]:
    """You posted a held main post yourself (Mark as posted): schedule what was waiting for it,
    such as its Trial Reel."""
    store = JobStore(settings.db_path)
    parent = store.get(parent_key)
    if parent is None:
        return []
    held = [r.job for r in store.rows(post_id=parent.job.post_id, limit=1000) if r.status == "held"]
    chain = {parent_key}
    followers: list[PostJob] = []
    for _ in range(3):  # trial reel, then the results check that follows it
        step = [j for j in held if j.depends_on in chain and j not in followers]
        followers += step
        chain |= {j.idem_key for j in step}
    return _schedule_followers(settings, store, followers, utcnow()) if followers else []


def find(store: JobStore, key_prefix: str):
    matches = [r for r in store.rows(limit=1000) if r.idem_key.startswith(key_prefix)]
    if not matches:
        raise VautoError(f"No job starting with {key_prefix!r} (see `vauto jobs`)")
    if len(matches) > 1:
        raise VautoError(f"{key_prefix!r} matches several jobs; use more characters")
    return matches[0]


def notify_results(settings: Settings, results: list[PostResult], header: str) -> None:
    worth = [r for r in results if r.status not in ("dry_run", "duplicate")]
    if worth:
        notify.send(settings, notify.results_text(worth, header))
