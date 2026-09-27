"""Operations shared by the CLI, the Telegram bot and the web app."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from . import notify, timing
from .config import Settings
from .errors import ConfigError, VautoError
from .media.variants import VariantOptions
from .models import PostJob, PostResult
from .pipeline import PostPlan, PostRequest, build_plan, execute
from .publishers import make_publisher
from .scheduler import JobStore, iso, run_job, utcnow
from .storage import make_storage


def resolve_publish_at(settings: Settings, at: str | None, platforms: list[str],
                       now: datetime | None = None) -> tuple[str | None, str | None]:
    """Turn an --at value into (ISO UTC time or None for now, human note)."""
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


def make_request(settings: Settings, inputs: list[Path], caption: str, platforms: list[str] | None = None,
                 **opts: Any) -> PostRequest:
    """Build a PostRequest with the user's defaults filled in."""
    platforms = [p.strip().lower() for p in (platforms or settings.default_platforms) if p.strip()]
    unknown = [p for p in platforms if p not in settings.backends]
    if unknown:
        raise ConfigError(f"No posting route for: {', '.join(unknown)}. See `vauto platforms`.")
    variant = opts.pop("variant", None) or VariantOptions(font_path=settings.font_path)
    if opts.get("trial") is None:
        opts["trial"] = settings.trial_default
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
    if row.status != "pending":
        raise VautoError(f"Only queued jobs can be cancelled; this one is {row.status}")
    result = PostResult(row.job.platform, row.job.surface, "skipped", error="cancelled by you")
    store.finish(row.idem_key, result)
    return result


def retry(settings: Settings, key_prefix: str) -> PostResult:
    store = JobStore(settings.db_path)
    row = find(store, key_prefix)
    if row.status not in ("failed", "skipped", "pending"):
        raise VautoError(f"Only failed, skipped or queued jobs can be retried; this one is {row.status}")
    job = row.job
    job.run_at = iso(utcnow())
    store.replace_pending(job)
    if store.get(job.idem_key) is None:
        store.insert(job, status="running")
    return run_job(store, job, publisher_factory(settings))


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
