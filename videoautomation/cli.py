"""`vauto` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings, implemented_platforms, load_platforms
from .errors import ConfigError, VautoError
from .ffmpeg import probe
from .media.normalize import FIT_MODES, normalize_video
from .media.variants import VariantOptions, render_variant
from .pipeline import PostRequest, build_plan, execute
from .publishers import ZernioPublisher, make_publisher
from .report import format_results, results_json
from .scheduler import JobStore, worker_loop
from .storage import make_storage


def _minutes(value: str) -> tuple[int, int]:
    parts = value.replace(" ", "").split("-")
    try:
        lo = int(parts[0])
        hi = int(parts[1]) if len(parts) > 1 else lo
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use minutes like 90 or 60-120") from exc
    if lo < 0 or hi < lo:
        raise argparse.ArgumentTypeError("use minutes like 90 or 60-120")
    return lo, hi


def _caption_for(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("use PLATFORM=TEXT, for example tiktok='new caption'")
    platform, text = value.split("=", 1)
    return platform.strip().lower(), text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vauto", description="Post one video or photo set to many platforms.")
    sub = parser.add_subparsers(dest="command", required=True)

    post = sub.add_parser("post", help="post a video or photos everywhere")
    post.add_argument("media", nargs="+", type=Path, help="one video, or one or more photos")
    text = post.add_mutually_exclusive_group(required=True)
    text.add_argument("-c", "--caption", help="caption text")
    text.add_argument("--caption-file", type=Path, help="read the caption from a file")
    post.add_argument("-p", "--platforms", help="comma-separated list (default: VAUTO_PLATFORMS or all Tier 1)")
    post.add_argument("--caption-for", action="append", type=_caption_for, default=[], metavar="PLATFORM=TEXT",
                      help="use a different caption on one platform (repeatable)")
    post.add_argument("--rewrite-captions", action="store_true", help="let Claude tailor the caption per platform")

    trial = post.add_argument_group("Instagram Trial Reel")
    trial.add_argument("--trial", dest="trial", action="store_true", default=None,
                       help="also post a zoomed Trial Reel later")
    trial.add_argument("--no-trial", dest="trial", action="store_false")
    trial.add_argument("--trial-delay", type=_minutes, help="minutes after the main post, e.g. 60-120 (random in range)")
    trial.add_argument("--trial-mode", choices=["static", "push"], default="static",
                       help="static zoom, or a slow push-in over the clip")
    trial.add_argument("--trial-zoom", type=float, default=1.15, help="zoom factor, default 1.15")
    trial.add_argument("--trial-hook", help="text shown at the top for the first seconds")
    trial.add_argument("--trial-hook-seconds", type=float, default=3.0)
    trial.add_argument("--trial-mirror", action="store_true", help="flip the video horizontally")
    trial.add_argument("--trial-speed", type=float, default=1.0, help="playback speed, e.g. 1.03")
    trial.add_argument("--trial-graduation", choices=["MANUAL", "SS_PERFORMANCE"],
                       help="MANUAL keeps it trial-only; SS_PERFORMANCE lets Instagram share it if it does well")
    trial.add_argument("--trial-caption", help="caption for the trial reel (default: the Instagram caption)")

    media = post.add_argument_group("media")
    media.add_argument("--fit", choices=FIT_MODES, default="auto",
                       help="how non-vertical media fills 9:16 (auto crops near-vertical, blurs the rest)")
    media.add_argument("--trim", action="store_true", help="cut videos down to each platform's maximum length")
    media.add_argument("--audio", type=Path, help="soundtrack for photo slideshows")
    media.add_argument("--slide-seconds", type=float, default=3.0, help="seconds per photo in slideshows")
    media.add_argument("--cover-ms", type=int, help="cover frame for Instagram/TikTok, in milliseconds")
    media.add_argument("--audio-name", help="name for the Reel's original audio on Instagram")

    extra = post.add_argument_group("extras")
    extra.add_argument("--tiktok-draft", action="store_true",
                       help="send to TikTok drafts so you can add a trending sound in the app")
    extra.add_argument("--ig-story", action="store_true", help="also post the video as an Instagram Story")
    extra.add_argument("--dry-run", action="store_true", help="render media and show the plan without posting")
    extra.add_argument("--force", action="store_true", help="post again even if this exact post was already sent")
    extra.add_argument("--json", action="store_true", help="print results as JSON")

    worker = sub.add_parser("worker", help="run queued posts (Trial Reels on direct Meta) when due")
    worker.add_argument("--once", action="store_true", help="run what is due now and exit (for cron)")
    worker.add_argument("--interval", type=float, default=30.0, help="seconds between checks")

    jobs = sub.add_parser("jobs", help="list recent and pending jobs")
    jobs.add_argument("--pending", action="store_true", help="only pending jobs")
    jobs.add_argument("--limit", type=int, default=30)

    plats = sub.add_parser("platforms", help="list researched platforms and which are implemented")
    plats.add_argument("--tier", type=int)

    sub.add_parser("accounts", help="list social accounts connected in Zernio")

    probe_cmd = sub.add_parser("probe", help="show media details")
    probe_cmd.add_argument("files", nargs="+", type=Path)

    variant = sub.add_parser("variant", help="render a Trial Reel variant locally to preview it")
    variant.add_argument("video", type=Path)
    variant.add_argument("-o", "--output", type=Path, required=True)
    variant.add_argument("--mode", choices=["static", "push"], default="static")
    variant.add_argument("--zoom", type=float, default=1.15)
    variant.add_argument("--hook")
    variant.add_argument("--hook-seconds", type=float, default=3.0)
    variant.add_argument("--mirror", action="store_true")
    variant.add_argument("--speed", type=float, default=1.0)
    variant.add_argument("--fit", choices=FIT_MODES, default="auto")
    return parser


# ------------------------------------------------------------------ commands


def cmd_post(args: argparse.Namespace, settings: Settings) -> int:
    caption = args.caption if args.caption is not None else args.caption_file.read_text(encoding="utf-8")
    platforms = [p.strip().lower() for p in (args.platforms or ",".join(settings.default_platforms)).split(",") if p.strip()]
    unknown = [p for p in platforms if p not in implemented_platforms()]
    if unknown:
        raise ConfigError(f"Not implemented yet: {', '.join(unknown)}. Available: {', '.join(implemented_platforms())}")
    overrides = dict(args.caption_for)
    bad = [p for p in overrides if p not in platforms]
    if bad:
        raise ConfigError(f"--caption-for names platforms not in this post: {', '.join(bad)}")

    req = PostRequest(
        inputs=args.media,
        caption=caption,
        platforms=platforms,
        caption_overrides=overrides,
        trial=settings.trial_default if args.trial is None else args.trial,
        trial_delay=args.trial_delay or settings.trial_delay_minutes,
        trial_graduation=args.trial_graduation or settings.trial_graduation,
        trial_caption=args.trial_caption,
        variant=VariantOptions(mode=args.trial_mode, zoom=args.trial_zoom, mirror=args.trial_mirror,
                               speed=args.trial_speed, hook_text=args.trial_hook,
                               hook_seconds=args.trial_hook_seconds, font_path=settings.font_path),
        tiktok_draft=args.tiktok_draft,
        ig_story=args.ig_story,
        fit=args.fit,
        allow_trim=args.trim,
        audio=args.audio,
        slide_seconds=args.slide_seconds,
        cover_ms=args.cover_ms,
        audio_name=args.audio_name,
        rewrite_captions=args.rewrite_captions,
        dry_run=args.dry_run,
        force=args.force,
    )
    if req.trial:
        req.variant.validate()
    plan = build_plan(req, settings)
    results = execute(plan, settings, req)
    if args.json:
        print(results_json(plan.post_id, results, plan.notes))
    else:
        header = f"post {plan.post_id} ({plan.kind})" + ("  [dry run: nothing was posted]" if req.dry_run else "")
        print(header)
        print(format_results(results, plan.notes))
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    store = JobStore(settings.db_path)
    storage = make_storage(settings)
    cache: dict = {}

    def publisher_for(job):
        key = (job.backend, job.platform)
        if key not in cache:
            cache[key] = make_publisher(job.backend, job.platform, settings, storage)
        return cache[key]

    failed = False
    if not args.once:
        print(f"vauto worker: checking every {args.interval:g}s (Ctrl+C to stop)")
    try:
        for result in worker_loop(store, publisher_for, interval=args.interval, once=args.once):
            failed = failed or result.status == "failed"
            print(format_results([result]), flush=True)
    except KeyboardInterrupt:
        return 0
    return 1 if failed else 0


def cmd_jobs(args: argparse.Namespace, settings: Settings) -> int:
    if not settings.db_path.is_file():
        print("No jobs yet.")
        return 0
    store = JobStore(settings.db_path)
    rows = store.rows(include_done=not args.pending, limit=args.limit)
    if not rows:
        print("No jobs.")
    for row in rows:
        r = row.result
        detail = (r.url or r.error or "") if r else ""
        print(f"{row.run_at}  {row.job.post_id}  {row.job.label:<22} {row.status:<10} {detail}")
    return 0


def cmd_platforms(args: argparse.Namespace, settings: Settings) -> int:
    for key, spec in load_platforms().items():
        if args.tier and spec.get("tier") != args.tier:
            continue
        status = "ready" if spec.get("status") == "implemented" else "planned"
        route = spec.get("route") or ", ".join(spec.get("backends", []))
        regions = ",".join(spec.get("regions", []))
        print(f"T{spec.get('tier')}  {key:<18} {status:<8} {regions:<14} {route}")
    return 0


def cmd_accounts(args: argparse.Namespace, settings: Settings) -> int:
    accounts = ZernioPublisher(settings).list_accounts()
    if not accounts:
        print("No accounts connected in Zernio yet.")
    for acc in accounts:
        acc_id = acc.get("_id") or acc.get("id") or acc.get("accountId")
        platform = acc.get("platform", "?")
        name = acc.get("username") or acc.get("displayName") or ""
        print(f"{platform:<12} {acc_id}  {name}   -> ZERNIO_ACCOUNT_{platform.upper()}={acc_id}")
    return 0


def cmd_probe(args: argparse.Namespace, settings: Settings) -> int:
    for path in args.files:
        info = probe(path)
        print(json.dumps(info.__dict__, indent=2))
    return 0


def cmd_variant(args: argparse.Namespace, settings: Settings) -> int:
    src = probe(args.video)
    master_path = args.output.with_name(args.output.stem + "_master.mp4")
    _, master = normalize_video(src, master_path, args.fit)
    opts = VariantOptions(mode=args.mode, zoom=args.zoom, mirror=args.mirror, speed=args.speed,
                          hook_text=args.hook, hook_seconds=args.hook_seconds, font_path=settings.font_path)
    info = render_variant(master, args.output, opts)
    print(f"variant: {info.path} ({info.width}x{info.height}, {info.duration:.1f}s)")
    print(f"master:  {master.path}")
    return 0


COMMANDS = {
    "post": cmd_post, "worker": cmd_worker, "jobs": cmd_jobs, "platforms": cmd_platforms,
    "accounts": cmd_accounts, "probe": cmd_probe, "variant": cmd_variant,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings.from_env()
        return COMMANDS[args.command](args, settings)
    except VautoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
