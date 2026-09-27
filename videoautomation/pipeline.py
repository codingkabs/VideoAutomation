"""Turn one post request into per-platform jobs, then run or schedule them."""

from __future__ import annotations

import hashlib
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from . import captions as cap
from .config import Settings, platform_spec
from .errors import MediaError, VautoError
from .ffmpeg import probe
from .media.normalize import normalize_video
from .media.photos import make_slideshow, normalize_image
from .media.renditions import make_rendition, plan_rendition
from .media.subtitles import Cue, burn_subtitles, cues_for
from .media.variants import VariantOptions, render_variant
from .models import MediaFile, MediaInfo, PostJob, PostResult
from .publishers import make_publisher
from .scheduler import JobStore, iso, parse_iso, run_job, utcnow
from .storage import Storage, make_storage

ACTIVE_STATUSES = {"published", "scheduled", "draft", "submitted", "pending", "running", "handoff", "reported"}
PARENT_OK = {"published", "submitted", "scheduled", "draft", "handoff"}
IG_STORY_VIDEO = {"min_s": 3, "max_s": 60, "max_mb": 100}
PHOTO_SLIDESHOW_MIN_S = 6.0

Progress = Callable[[str], None]


def _quiet(_: str) -> None:
    pass


@dataclass
class PostRequest:
    inputs: list[Path]
    caption: str
    platforms: list[str]
    caption_overrides: dict[str, str] = field(default_factory=dict)
    publish_at: str | None = None  # ISO UTC; None = now
    trial: bool = False
    trial_delay: tuple[int, int] = (60, 120)
    trial_graduation: str = "MANUAL"
    trial_caption: str | None = None
    trial_report: bool = True
    variant: VariantOptions = field(default_factory=VariantOptions)
    tiktok_draft: bool = False
    ig_story: bool = False
    fit: str = "auto"
    allow_trim: bool = False
    audio: Path | None = None
    slide_seconds: float = 3.0
    cover_ms: int | None = None
    audio_name: str | None = None
    subtitles: str | None = None  # "auto" or a .srt/.vtt path
    subtitle_style: str | None = None
    rewrite_captions: bool = False
    dry_run: bool = False
    force: bool = False
    progress: Progress = field(default=_quiet, repr=False, compare=False)


@dataclass
class PostPlan:
    post_id: str
    kind: str  # "video" or "photos"
    jobs: list[PostJob]
    results: list[PostResult]  # platforms rejected while planning
    notes: list[str] = field(default_factory=list)
    work_dir: str = ""


# ------------------------------------------------------------------ helpers


def file_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()


def idem_key(media_hash: str, platform: str, surface: str, caption: str, extra: str = "") -> str:
    raw = "|".join([media_hash, platform, surface, caption, extra])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def default_surface(platform: str, kind: str, settings: Settings) -> str:
    surfaces = platform_spec(platform).get("surfaces") or {}
    if platform == "snapchat" and kind == "video":
        return settings.snapchat_content_type
    return surfaces.get(kind) or surfaces.get("video") or "video"


class CachedStorage:
    """Upload each file once even when several platforms share it."""

    def __init__(self, inner: Storage):
        self.inner = inner
        self.name = inner.name
        self._urls: dict[str, str] = {}
        self._lock = threading.Lock()

    def put(self, path: Path, key: str) -> str:
        with self._lock:
            if str(path) not in self._urls:
                self._urls[str(path)] = self.inner.put(path, key)
            return self._urls[str(path)]


# ------------------------------------------------------------------ planning


def _captions_for(req: PostRequest, settings: Settings, notes: list[str]) -> dict[str, str]:
    base = {p: req.caption for p in req.platforms}
    if req.rewrite_captions:
        req.progress("Rewriting captions with Claude")
        limits = {p: int(platform_spec(p)["caption"]["max_chars"]) for p in req.platforms}
        try:
            base.update(cap.rewrite_captions(req.caption, req.platforms, limits, settings.caption_model,
                                            settings.anthropic_api_key))
            notes.append(f"captions rewritten per platform by {settings.caption_model}")
        except VautoError as exc:
            notes.append(f"caption rewrite skipped, using your caption as written: {exc}")
    base.update({p: text for p, text in req.caption_overrides.items() if p in base})
    return base


def _adapted(platform: str, text: str, surface: str, settings: Settings) -> cap.PlatformCaption:
    return cap.adapt(platform, text, platform_spec(platform)["caption"], surface,
                     youtube_shorts_tag=settings.youtube_add_shorts_tag,
                     snapchat_content_type=settings.snapchat_content_type)


def _options(platform: str, surface: str, adapted: cap.PlatformCaption, req: PostRequest,
             settings: Settings, duration: float | None) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if duration:
        opts["duration_s"] = round(duration, 2)
    if adapted.title:
        opts["title"] = adapted.title
    if adapted.tags:
        opts["tags"] = adapted.tags
    if platform == "youtube":
        opts["visibility"] = settings.youtube_visibility
    if platform == "tiktok":
        opts["draft"] = req.tiktok_draft or settings.backends.get("tiktok") == "tiktok"
        if surface == "photos":
            opts.update(auto_add_music=True, description=adapted.text)
    if platform == "snapchat":
        opts["content_type"] = settings.snapchat_content_type
    if platform in ("instagram", "tiktok", "pinterest") and req.cover_ms is not None and surface != "photos":
        opts["thumb_offset_ms"] = req.cover_ms
    if platform == "instagram" and req.audio_name:
        opts["audio_name"] = req.audio_name
    if platform == "pinterest" and settings.pinterest_board_id:
        opts["board_id"] = settings.pinterest_board_id
    if platform == "reddit":
        if settings.reddit_subreddit:
            opts["subreddit"] = settings.reddit_subreddit
        if settings.reddit_flair_id:
            opts["flair_id"] = settings.reddit_flair_id
    return opts


def build_plan(req: PostRequest, settings: Settings) -> PostPlan:
    if not req.inputs:
        raise VautoError("Give a video file, or one or more photos")
    if not req.platforms:
        raise VautoError("No platforms selected")
    for p in req.platforms:
        platform_spec(p)  # validates the name
        if p not in settings.backends:
            raise VautoError(f"{p} has no posting route yet (see `vauto platforms`)")
    if req.publish_at and parse_iso(req.publish_at) <= utcnow():
        raise VautoError("The scheduled time is in the past")

    req.progress("Reading media")
    infos = [probe(p) for p in req.inputs]
    images = [i for i in infos if i.is_image]
    if images and len(images) != len(infos):
        raise VautoError("Mixing videos and photos in one post is not supported")
    if not images and len(infos) > 1:
        raise VautoError("Give one video per post")
    kind = "photos" if images else "video"

    media_hash = file_hash([Path(i.path) for i in infos])
    post_id = media_hash[:12]
    work = settings.renders_dir / post_id
    work.mkdir(parents=True, exist_ok=True)
    nonce = utcnow().isoformat() if req.force else ""

    notes: list[str] = []
    texts = _captions_for(req, settings, notes)
    plan = PostPlan(post_id=post_id, kind=kind, jobs=[], results=[], notes=notes, work_dir=str(work))

    def add_job(platform: str, surface: str, media: list[MediaFile], adapted: cap.PlatformCaption,
                opts: dict[str, Any], extra: str = "", run_at: str | None = None,
                depends_on: str | None = None, caption_text: str | None = None,
                backend: str | None = None) -> PostJob:
        caption_text = adapted.text if caption_text is None else caption_text
        job = PostJob(
            platform=platform, surface=surface, backend=backend or settings.backends[platform], media=media,
            caption=caption_text, options=opts, post_id=post_id,
            run_at=req.publish_at if run_at is None else run_at, depends_on=depends_on,
            idem_key=idem_key(media_hash, platform, surface, caption_text, extra + nonce),
        )
        for note in adapted.notes:
            plan.notes.append(f"{platform}: {note}")
        plan.jobs.append(job)
        return job

    def reject(platform: str, surface: str, error: str) -> None:
        plan.results.append(PostResult(platform, surface, "failed", error=error))

    if kind == "video":
        _plan_video(req, settings, infos[0], work, texts, plan, add_job, reject)
    else:
        _plan_photos(req, settings, images, work, texts, plan, add_job, reject)
    plan.notes = list(dict.fromkeys(plan.notes))
    return plan


def _subtitle_cues(req: PostRequest, settings: Settings, master: MediaInfo, work: Path,
                   plan: PostPlan) -> list[Cue] | None:
    if not req.subtitles:
        return None
    req.progress("Transcribing speech for subtitles" if req.subtitles == "auto" else "Loading subtitles")
    cues = cues_for(master, req.subtitles, work, settings.subtitle_model, settings.subtitle_language)
    if req.subtitles == "auto":
        plan.notes.append(f"subtitles: {len(cues)} captions; fix any word in {work / 'subtitles.srt'} and post again")
    return cues


def _plan_video(req, settings, src: MediaInfo, work: Path, texts, plan, add_job, reject) -> None:
    master_path = work / f"master_{req.fit}.mp4"
    if master_path.is_file():
        clean = probe(master_path)
    else:
        req.progress("Converting video to vertical 1080x1920")
        _, clean = normalize_video(src, master_path, req.fit)

    style = req.subtitle_style or settings.subtitle_style
    cues = _subtitle_cues(req, settings, clean, work, plan)
    master = clean
    if cues:
        subbed = work / f"master_{req.fit}_subs_{style}.mp4"
        req.progress("Burning in subtitles")
        master = burn_subtitles(clean, subbed, cues, style)

    main_ig: PostJob | None = None
    req.progress(f"Preparing {len(req.platforms)} platform version(s)")
    for platform in req.platforms:
        spec = platform_spec(platform)
        surface = default_surface(platform, "video", settings)
        try:
            rplan = plan_rendition(master, spec["video"], spec["name"], req.allow_trim)
            rendition = make_rendition(master, rplan, work)
        except MediaError as exc:
            reject(platform, surface, str(exc))
            continue
        adapted = _adapted(platform, texts[platform], surface, settings)
        adapted.notes.extend(rplan.notes)
        opts = _options(platform, surface, adapted, req, settings, rendition.duration)
        job = add_job(platform, surface, [MediaFile(rendition.path, "video")], adapted, opts)
        if platform == "instagram":
            main_ig = job

    if req.ig_story and "instagram" in req.platforms:
        try:
            rplan = plan_rendition(master, IG_STORY_VIDEO, "Instagram Stories", True)
            story = make_rendition(master, rplan, work)
            adapted = cap.PlatformCaption(text="", notes=list(rplan.notes))
            add_job("instagram", "story", [MediaFile(story.path, "video")], adapted, {})
        except MediaError as exc:
            reject("instagram", "story", str(exc))

    if req.trial:
        if main_ig is None:
            plan.notes.append("trial reel skipped: Instagram is not in this post or its main Reel was rejected")
            return
        _plan_trial(req, settings, clean, cues, style, work, texts, plan, add_job, reject, main_ig)


def _plan_trial(req, settings, clean: MediaInfo, cues, style, work: Path, texts, plan, add_job, reject,
                main_ig: PostJob) -> None:
    opts_v = req.variant
    sig = hashlib.sha256(repr((opts_v.mode, opts_v.zoom, opts_v.mirror, opts_v.speed, opts_v.hook_text,
                               opts_v.hook_seconds, bool(cues), style)).encode()).hexdigest()[:10]
    variant_path = work / f"trial_{sig}.mp4"
    try:
        if variant_path.is_file():
            variant = probe(variant_path)
        else:
            req.progress("Rendering the zoomed Trial Reel version")
            raw_path = work / f"trial_{sig}_raw.mp4"
            variant = render_variant(clean, raw_path if cues else variant_path, opts_v)
            if cues:
                # Subtitles go on after the zoom so they are never cropped.
                variant = burn_subtitles(variant, variant_path, cues, style, speed=opts_v.speed)
        spec = platform_spec("instagram")
        rplan = plan_rendition(variant, spec["video"], spec["name"], req.allow_trim)
        variant = make_rendition(variant, rplan, work)
    except MediaError as exc:
        reject("instagram", "trial_reel", str(exc))
        return

    text = req.trial_caption if req.trial_caption is not None else texts["instagram"]
    adapted = _adapted("instagram", text, "trial_reel", settings)
    lo, hi = req.trial_delay
    base = parse_iso(req.publish_at) if req.publish_at else utcnow()
    trial_at = base + timedelta(minutes=random.randint(lo, hi))

    main_cover = main_ig.options.get("thumb_offset_ms")
    duration_ms = int(variant.duration * 1000)
    cover = int(duration_ms * 0.4) if main_cover is None else (main_cover + duration_ms // 3) % max(duration_ms, 1)
    opts = {"trial_graduation": req.trial_graduation, "thumb_offset_ms": cover,
            "duration_s": round(variant.duration, 2)}
    if req.audio_name:
        opts["audio_name"] = req.audio_name
    trial = add_job("instagram", "trial_reel", [MediaFile(variant.path, "video")], adapted, opts,
                    extra=sig, run_at=iso(trial_at), depends_on=main_ig.idem_key,
                    backend=settings.trial_backend_for())

    if req.trial_report:
        report_at = trial_at + timedelta(hours=settings.trial_report_hours)
        add_job("instagram", "trial_report", [], cap.PlatformCaption(text=""), {"trial_key": trial.idem_key},
                extra="report" + sig, run_at=iso(report_at), depends_on=trial.idem_key, backend="insights",
                caption_text="")


def _plan_photos(req, settings, images: list[MediaInfo], work: Path, texts, plan, add_job, reject) -> None:
    def normalized(tag: str, photo_spec: dict[str, Any], limit: int) -> list[MediaFile]:
        size = tuple(photo_spec["size"]) if photo_spec.get("size") else None
        max_bytes = int(float(photo_spec["max_mb"]) * 1024 * 1024) if photo_spec.get("max_mb") else None
        files = []
        for i, img in enumerate(images[:limit]):
            out = work / f"photo_{tag}_{i:02d}.jpg"
            if not out.is_file():
                normalize_image(img, out, size, req.fit, max_bytes)
            files.append(MediaFile(str(out), "image"))
        return files

    slideshow: MediaInfo | None = None
    req.progress(f"Preparing {len(req.platforms)} platform version(s)")
    for platform in req.platforms:
        spec = platform_spec(platform)
        surface = default_surface(platform, "photos", settings)
        photo_spec = spec.get("photos") if (spec.get("surfaces") or {}).get("photos") else None
        adapted = _adapted(platform, texts[platform], surface, settings)
        try:
            if photo_spec:
                limit = int(photo_spec["max_items"])
                if len(images) > limit:
                    adapted.notes.append(f"only the first {limit} of {len(images)} photos were used")
                media = normalized(platform, photo_spec, limit)
                if platform == "instagram" and len(media) == 1:
                    surface = "image"
                opts = _options(platform, surface, adapted, req, settings, None)
                caption_text = adapted.title if platform == "tiktok" else None
                add_job(platform, surface, media, adapted, opts, caption_text=caption_text)
            else:
                if slideshow is None:
                    req.progress("Making a slideshow video from the photos")
                    per_image = max(req.slide_seconds, PHOTO_SLIDESHOW_MIN_S / len(images))
                    out = work / f"slideshow_{req.fit}_{per_image:.2f}.mp4"
                    slideshow = probe(out) if out.is_file() else make_slideshow(
                        images, out, per_image, req.audio, req.fit)[1]
                rplan = plan_rendition(slideshow, spec["video"], spec["name"], req.allow_trim)
                video = make_rendition(slideshow, rplan, work)
                adapted.notes.append("photos turned into a slideshow video")
                adapted.notes.extend(rplan.notes)
                surface = default_surface(platform, "video", settings)
                opts = _options(platform, surface, adapted, req, settings, video.duration)
                add_job(platform, surface, [MediaFile(video.path, "video")], adapted, opts)
        except MediaError as exc:
            reject(platform, surface, str(exc))

    if req.trial:
        plan.notes.append("trial reels need a video; skipped for this photo post")


# ------------------------------------------------------------------ execution


def execute(plan: PostPlan, settings: Settings, req: PostRequest) -> list[PostResult]:
    store = JobStore(Path(":memory:") if req.dry_run else settings.db_path)
    raw_storage = make_storage(settings, dry_run=req.dry_run)
    storage = CachedStorage(raw_storage) if raw_storage else None
    publishers: dict[tuple[str, str], Any] = {}
    lock = threading.Lock()

    def publisher_for(job: PostJob):
        key = (job.backend, job.platform)
        with lock:
            if key not in publishers:
                publishers[key] = make_publisher(job.backend, job.platform, settings, storage, req.dry_run)
            return publishers[key]

    results: list[PostResult] = list(plan.results)
    now_jobs, later_jobs = [], []
    for job in plan.jobs:
        existing = store.get(job.idem_key)
        if existing and existing.status in ACTIVE_STATUSES and not req.force:
            prior = existing.result
            results.append(PostResult(job.platform, job.surface, "duplicate",
                                      url=prior.url if prior else None, run_at=existing.run_at,
                                      notes=[f"already {existing.status}; use --force to post again"]))
            continue
        if existing:
            store.replace_pending(job)
        due = job.run_at is None or parse_iso(job.run_at) <= utcnow()
        (now_jobs if due else later_jobs).append(job)

    if now_jobs:
        req.progress(f"Posting to {len(now_jobs)} destination(s)" + (" (dry run)" if req.dry_run else ""))
    for job in now_jobs:
        store.insert(job, status="running")
    with ThreadPoolExecutor(max_workers=max(1, min(6, len(now_jobs)))) as pool:
        results.extend(pool.map(lambda j: run_job(store, j, publisher_for), now_jobs))

    for job in later_jobs:
        try:
            publisher = publisher_for(job)
        except VautoError as exc:
            results.append(PostResult(job.platform, job.surface, "failed", error=str(exc)))
            continue
        if req.dry_run:
            results.append(publisher.publish(job))
            continue
        parent = store.get(job.depends_on) if job.depends_on else None
        if job.depends_on and (parent is None or parent.status in ("failed", "skipped")):
            results.append(PostResult(job.platform, job.surface, "skipped",
                                      error="skipped because the post it follows did not go out"))
            continue
        parent_waiting = parent is not None and parent.status in ("pending", "running")
        if publisher.schedules_remotely(job) and not parent_waiting:
            store.insert(job, status="running")
            results.append(run_job(store, job, publisher_for))
        else:
            store.insert(job, status="pending")
            note = ("results check; needs `vauto worker` running then" if job.surface == "trial_report"
                    else "waits in the local queue; keep `vauto worker` running")
            results.append(PostResult(job.platform, job.surface, "queued", run_at=job.run_at, notes=[note]))
    return results
