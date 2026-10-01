"""`vauto` command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import auth, service
from .config import Settings, load_platforms, platform_spec
from .doctor import platform_ready, run_checks
from .envfile import update_env
from .errors import ConfigError, VautoError
from .models import label_for
from .notify import ICONS
from .ffmpeg import probe
from .media.normalize import FIT_MODES, normalize_video
from .media.variants import VariantOptions, render_variant
from .publishers import ZernioPublisher
from .report import format_checks, format_results, paint, results_json, summary_line, use_colour, when
from .scheduler import JobStore, worker_loop
from .timing import zone


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
    parser = argparse.ArgumentParser(
        prog="vauto", description="Post one video or photo set to many platforms.",
        epilog="Start with `vauto doctor` to see what is set up, or `vauto web` for the app.")
    sub = parser.add_subparsers(dest="command", required=True)

    post = sub.add_parser("post", help="post a video or photos everywhere")
    post.add_argument("media", nargs="+", type=Path, help="one video, or one or more photos")
    text = post.add_mutually_exclusive_group(required=True)
    text.add_argument("-c", "--caption", help="caption text")
    text.add_argument("--caption-file", type=Path, help="read the caption from a file")
    post.add_argument("-p", "--platforms", help="comma-separated list (default: VAUTO_PLATFORMS)")
    post.add_argument("--caption-for", action="append", type=_caption_for, default=[], metavar="PLATFORM=TEXT",
                      help="use a different caption on one platform (repeatable)")
    post.add_argument("--rewrite-captions", action="store_true", help="let Claude tailor the caption per platform")
    post.add_argument("--at", help="when to post: now, best, 18:30, 6pm, tomorrow 9am, +2h, 2026-10-01 18:30")

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
    trial.add_argument("--no-trial-report", dest="trial_report", action="store_false", default=True,
                       help="skip the results check 72 hours later")

    media = post.add_argument_group("media")
    media.add_argument("--fit", choices=FIT_MODES, default="auto",
                       help="how non-vertical media fills 9:16 (auto crops near-vertical, blurs the rest)")
    media.add_argument("--trim", action="store_true", help="cut videos down to each platform's maximum length")
    media.add_argument("--subtitles", metavar="auto|FILE", help="burn in subtitles: auto (speech-to-text) or a .srt/.vtt")
    media.add_argument("--subtitle-style", choices=["bold", "clean"], help="subtitle look (default bold)")
    media.add_argument("--audio", type=Path, help="soundtrack for photo slideshows")
    media.add_argument("--slide-seconds", type=float, default=3.0, help="seconds per photo in slideshows")
    media.add_argument("--cover-ms", type=int, help="cover frame for Instagram/TikTok/Pinterest, in milliseconds")
    media.add_argument("--audio-name", help="name for the Reel's original audio on Instagram")

    extra = post.add_argument_group("extras")
    extra.add_argument("--tiktok-draft", action="store_true",
                       help="send to TikTok drafts so you can add a trending sound in the app")
    extra.add_argument("--first-comment", metavar="TEXT|claude|off",
                       help="first comment on Instagram, Facebook, YouTube and LinkedIn: your text, "
                            "'claude' to have Claude write one that fits the video, or 'off'")
    extra.add_argument("--drafts", dest="drafts", action="store_const", const=True, default=None,
                       help="save as drafts instead of publishing: TikTok drafts, YouTube private, the rest held "
                            "in vauto until `vauto posts --publish ID`")
    extra.add_argument("--draft-only", metavar="PLATFORMS",
                       help="draft only these platforms and publish the rest, e.g. --draft-only instagram")
    extra.add_argument("--no-drafts", dest="drafts", action="store_const", const=False,
                       help="publish (overrides VAUTO_DRAFTS_DEFAULT)")
    extra.add_argument("--ig-story", action="store_true", help="also post the video as an Instagram Story")
    extra.add_argument("--dry-run", action="store_true", help="render media and show the plan without posting")
    extra.add_argument("--force", action="store_true", help="post again even if this exact post was already sent")
    extra.add_argument("--json", action="store_true", help="print results as JSON")
    extra.add_argument("-q", "--quiet", action="store_true", help="no progress messages")

    worker = sub.add_parser("worker", help="post queued jobs when they are due")
    worker.add_argument("--once", action="store_true", help="run what is due now and exit (for cron)")
    worker.add_argument("--interval", type=float, default=30.0, help="seconds between checks")

    jobs = sub.add_parser("jobs", help="list, cancel or retry posts")
    jobs.add_argument("--pending", action="store_true", help="only queued jobs")
    jobs.add_argument("--limit", type=int, default=30)
    jobs.add_argument("--cancel", metavar="ID", help="cancel a queued job (first characters of its id)")
    jobs.add_argument("--retry", metavar="ID", help="retry a failed job now")

    posts = sub.add_parser("posts", help="your post history: where each video went and how it did")
    posts.add_argument("post_id", nargs="?", help="show one post in detail (first characters of its id)")
    posts.add_argument("-s", "--search", default="", help="only posts whose caption contains this")
    posts.add_argument("--platform", default="", help="only posts on this platform")
    posts.add_argument("--show", choices=["all", "upcoming", "posted", "attention", "failed"], default="all",
                       help="attention = failed, or waiting for you to finish on your phone")
    posts.add_argument("--limit", type=int, default=15)
    posts.add_argument("--mark-posted", metavar="JOB_ID",
                       help="you finished a hand-off/browser post yourself: record it as live")
    posts.add_argument("--url", help="the post's link, with --mark-posted")
    posts.add_argument("--publish", metavar="POST_ID", help="post the drafts vauto is holding for this post")
    posts.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="views, likes and what works best, across platforms")
    stats.add_argument("--days", type=int, default=30, help="period to cover (0 = all time)")
    stats.add_argument("--refresh", action="store_true", help="fetch fresh numbers from the platforms first")
    stats.add_argument("--json", action="store_true")

    clean = sub.add_parser("clean", help="delete old rendered videos and uploads to free disk space")
    clean.add_argument("--days", type=int, help="delete files older than this (default VAUTO_KEEP_FILES_DAYS, 14)")
    clean.add_argument("--dry-run", action="store_true", help="only show what would be deleted")

    export = sub.add_parser("export", help="save your post history and numbers as a CSV spreadsheet")
    export.add_argument("output", type=Path, nargs="?", default=Path("vauto-posts.csv"))

    trials = sub.add_parser("trials", help="compare Trial Reels with their main Reels")
    trials.add_argument("--limit", type=int, default=5)

    plats = sub.add_parser("platforms", help="list every platform, or show how one works: vauto platforms tiktok")
    plats.add_argument("name", nargs="?", help="one platform, for its guide, limits and setup")
    plats.add_argument("--tier", type=int)
    plats.add_argument("--ready", action="store_true", help="only platforms that are set up")

    sub.add_parser("accounts", help="list social accounts connected in Zernio")

    doctor = sub.add_parser("doctor", help="check setup and show what is missing")
    doctor.add_argument("--online", action="store_true", help="also test API keys over the network")
    doctor.add_argument("--json", action="store_true")

    best = sub.add_parser("best-time", help="show the next best time to post")
    best.add_argument("-p", "--platforms", default="instagram")

    auth_cmd = sub.add_parser("auth", help="connect accounts and manage tokens")
    auth_sub = auth_cmd.add_subparsers(dest="auth_command", required=True)
    meta = auth_sub.add_parser("meta", help="turn a Graph API Explorer token into never-expiring Page tokens")
    meta.add_argument("--user-token", required=True)
    meta.add_argument("--page", help="Page id to use when you manage several")
    meta.add_argument("--write", action="store_true", help="save the ids and tokens into .env")
    tiktok = auth_sub.add_parser("tiktok", help="connect TikTok for free drafts (no audit)")
    tiktok.add_argument("--code", help="the code, or the whole URL TikTok redirected you to")
    auth_sub.add_parser("refresh", help="refresh Instagram/Threads/TikTok tokens now")
    auth_sub.add_parser("status", help="show stored tokens and expiry")

    browser = sub.add_parser("browser", help="browser automation for Rutube, Likee, Dzen, Naver Clip")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    login = browser_sub.add_parser("login", help="open a browser to log in once and save the session")
    login.add_argument("platform")
    check = browser_sub.add_parser("check", help="check the saved login reaches the upload page")
    check.add_argument("platform")

    sub.add_parser("bot", help="run the Telegram bot: send it a video and caption from your phone")

    phone = sub.add_parser("phone", help="show how to use vauto from your phone, with your exact values")
    phone.add_argument("--port", type=int, default=8765)

    web = sub.add_parser("web", help="open the web app")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)

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


def _draft_opts(drafts: bool | None, only: str | None) -> dict:
    """--drafts (every platform), --draft-only instagram,tiktok (just those), --no-drafts, or the setting."""
    if only:
        return {"drafts": True, "draft_platforms": [p.strip().lower() for p in only.split(",") if p.strip()]}
    if drafts is None:
        return {}
    return {"drafts": drafts, "draft_platforms": [] if drafts else None}


def _comment_opts(value: str | None) -> dict:
    """--first-comment: None keeps the VAUTO_FIRST_COMMENT default."""
    if value is None:
        return {}
    word = value.strip().lower()
    if word in ("off", "claude"):
        return {"first_comment_mode": word}
    return {"first_comment_mode": "mine", "first_comment": value.strip()}


def cmd_post(args: argparse.Namespace, settings: Settings) -> int:
    caption = args.caption if args.caption is not None else args.caption_file.read_text(encoding="utf-8")
    platforms = (args.platforms.split(",") if args.platforms else None)
    overrides = dict(args.caption_for)
    chosen = [p.strip().lower() for p in (platforms or settings.default_platforms)]
    bad = [p for p in overrides if p not in chosen]
    if bad:
        raise ConfigError(f"--caption-for names platforms not in this post: {', '.join(bad)}")
    colour = use_colour() and not args.json

    def progress(message: str) -> None:
        if not args.quiet and not args.json:
            print(paint(f"… {message}", "off", colour), file=sys.stderr, flush=True)

    publish_at, when_note = service.resolve_publish_at(settings, args.at, chosen)
    req = service.make_request(
        settings, args.media, caption, platforms,
        caption_overrides=overrides, publish_at=publish_at,
        trial=args.trial, trial_delay=args.trial_delay, trial_graduation=args.trial_graduation,
        trial_caption=args.trial_caption, trial_report=args.trial_report,
        variant=VariantOptions(mode=args.trial_mode, zoom=args.trial_zoom, mirror=args.trial_mirror,
                               speed=args.trial_speed, hook_text=args.trial_hook,
                               hook_seconds=args.trial_hook_seconds, font_path=settings.font_path),
        tiktok_draft=args.tiktok_draft, ig_story=args.ig_story, fit=args.fit,
        **_draft_opts(args.drafts, args.draft_only),
        **_comment_opts(args.first_comment),
        allow_trim=args.trim,
        audio=args.audio, slide_seconds=args.slide_seconds, cover_ms=args.cover_ms, audio_name=args.audio_name,
        subtitles=args.subtitles, subtitle_style=args.subtitle_style,
        rewrite_captions=args.rewrite_captions, dry_run=args.dry_run, force=args.force, progress=progress,
    )
    plan, results = service.post(settings, req)
    if when_note:
        plan.notes.insert(0, when_note)
    if args.json:
        print(results_json(plan.post_id, results, plan.notes))
    else:
        header = f"post {plan.post_id} ({plan.kind})"
        if req.dry_run:
            header += "  [preview only: nothing was posted]"
        print(paint(header, "off", colour) if colour else header)
        print(format_results(results, plan.notes, colour, zone(settings)))
        print(f"\n{summary_line(results)}")
    if not req.dry_run:
        service.notify_results(settings, results, f"vauto post {plan.post_id}")
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    from .tracker import Tracker, housekeeping

    store = JobStore(settings.db_path)
    tracker = Tracker(store)
    publisher_for = service.publisher_factory(settings)
    colour = use_colour()
    failed = False
    if not args.once:
        print(f"vauto worker: checking every {args.interval:g}s (Ctrl+C to stop)")

    def tick() -> None:
        try:
            for line in housekeeping(settings, tracker):
                print(line, flush=True)
        except Exception as exc:  # numbers and summaries must never stop posting
            print(f"stats refresh problem: {exc}", flush=True)

    try:
        for result in worker_loop(store, publisher_for, interval=args.interval, once=args.once, tick=tick):
            failed = failed or result.status == "failed"
            print(format_results([result], colour=colour, tz=zone(settings)), flush=True)
            if result.status not in ("queued", "reported"):  # reports message you themselves
                service.notify_results(settings, [result], "vauto worker")
    except KeyboardInterrupt:
        return 0
    return 1 if failed else 0


def cmd_jobs(args: argparse.Namespace, settings: Settings) -> int:
    if args.cancel:
        print(format_results([service.cancel(settings, args.cancel)]))
        return 0
    if args.retry:
        result = service.retry(settings, args.retry)
        print(format_results([result], colour=use_colour()))
        return 1 if result.status == "failed" else 0
    if not settings.db_path.is_file():
        print("No posts yet.")
        return 0
    rows = JobStore(settings.db_path).rows(include_done=not args.pending, limit=args.limit)
    if not rows:
        print("No jobs.")
    colour = use_colour()
    for row in rows:
        r = row.result
        detail = (r.url or r.error or "") if r else ""
        status = paint(f"{row.status:<10}", row.status if row.status != "pending" else "queued", colour)
        print(f"{row.idem_key[:8]}  {when(row.run_at, zone(settings)):<20} {row.job.label:<22} {status} {detail}")
    return 0


def cmd_trials(args: argparse.Namespace, settings: Settings) -> int:
    from .insights import compare, trial_keys

    if not settings.db_path.is_file():
        print("No Trial Reels yet.")
        return 0
    store = JobStore(settings.db_path)
    keys = trial_keys(store, args.limit)
    if not keys:
        print("No Trial Reels yet.")
    for key in keys:
        comparison = compare(settings, store, key)
        print(f"post {comparison.post_id}  main: {comparison.main_url or '-'}  trial: {comparison.trial_url or '-'}")
        for line in comparison.lines():
            print(f"  {line}")
        print()
    return 0


METRIC_ICONS = (("views", "👁"), ("likes", "♥"), ("comments", "💬"), ("shares", "↗"))


def _numbers(metrics: dict[str, float]) -> str:
    from .tracker import compact

    return "  ".join(f"{icon} {compact(metrics[k])}" for k, icon in METRIC_ICONS if metrics.get(k))


def cmd_posts(args: argparse.Namespace, settings: Settings) -> int:
    from .tracker import Tracker, short

    if not settings.db_path.is_file():
        print("No posts yet. Post something with `vauto post` or the web app.")
        return 0
    tracker = Tracker.open(settings)
    tz = zone(settings)
    colour = use_colour()
    if args.publish:
        matches = [p for p in tracker.posts(settings, limit=100000)["posts"] if p["post_id"].startswith(args.publish)]
        if len(matches) != 1:
            raise VautoError(f"No single post starting with {args.publish!r}")
        results = service.publish_drafts(settings, matches[0]["post_id"])
        print(format_results(results, colour=colour, tz=tz))
        service.notify_results(settings, results, f"vauto drafts posted {matches[0]['post_id']}")
        return 1 if any(r.status == "failed" for r in results) else 0
    if args.mark_posted:
        key = tracker.find(args.mark_posted).idem_key
        result = tracker.mark_posted(key, args.url)
        print(f"Marked {label_for(result.platform, result.surface)} as posted" + (f": {result.url}" if result.url else ""))
        for follower in service.release_followers(settings, key):
            print(f"{label_for(follower.platform, follower.surface)} goes out {when(follower.run_at, tz)}")
        return 0
    if args.post_id:
        matches = [p for p in tracker.posts(settings, limit=100000)["posts"] if p["post_id"].startswith(args.post_id)]
        if len(matches) != 1:
            raise VautoError(f"No single post starting with {args.post_id!r}")
        p = matches[0]
        if args.json:
            print(json.dumps(p, indent=2))
            return 0
        print(f"{p['post_id']}  {when(p['posted_at'], tz)}  {p['kind']}")
        print(f"  {p['caption']}\n")
        for j in p["jobs"]:
            status = paint(f"{j['status'].replace('_past', ''):<10}", j["status"].replace("_past", ""), colour)
            detail = j["url"] or j["error"] or (j["notes"][0] if j["notes"] else "")
            print(f"  {j['id']}  {j['label']:<24} {status} {_numbers(j['metrics'])}  {detail}".rstrip())
        for r in p["rejected"]:
            print(f"  {'':10}  {r['label']:<24} {paint('NOT POSTED', 'failed', colour)} {r['error']}")
        return 0
    show = "attention" if args.show == "failed" else args.show
    data = tracker.posts(settings, query=args.search, platform=args.platform, show=show, limit=args.limit)
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    if not data["posts"]:
        print("No posts match." if (args.search or args.platform or show != "all") else "No posts yet.")
        return 0
    for p in data["posts"]:
        marks = "  ".join(f"{ICONS.get(j['status'].replace('_past', ''), '•')} {j['label']}" for j in p["jobs"])
        print(f"{p['post_id'][:8]}  {when(p['posted_at'], tz):<20} {short(p['caption'], 44):<45} {_numbers(p['totals'])}")
        print(f"          {marks}" + (f"  (+{len(p['rejected'])} not posted)" if p["rejected"] else ""))
    if data["total"] > len(data["posts"]):
        print(f"\n{data['total'] - len(data['posts'])} more; use --limit or --search")
    return 0


def cmd_stats(args: argparse.Namespace, settings: Settings) -> int:
    from .stats import refresh
    from .tracker import Tracker, compact, short

    if not settings.db_path.is_file():
        print("No posts yet.")
        return 0
    tracker = Tracker.open(settings)
    if args.refresh:
        print("Fetching numbers: " + refresh(settings, tracker).text())
    s = tracker.summary(settings, days=args.days or None)
    if args.json:
        print(json.dumps(s, indent=2))
        return 0
    t = s["totals"]
    period = f"last {args.days} days" if args.days else "all time"
    print(f"{period}: {s['posts']} post(s), {s['live']} live across platforms"
          + (f", {s['failed']} failed" if s["failed"] else ""))
    if not s["has_numbers"]:
        print("No view counts yet. Run `vauto stats --refresh` once Instagram insights or Zernio analytics is set up,"
              " or type numbers in the web app (My posts).")
    else:
        print(f"👁 {compact(t['views'])} views   ♥ {compact(t['likes'])}   💬 {compact(t['comments'])}"
              f"   ↗ {compact(t['shares'])}   🔖 {compact(t['saved'])}")
        print("\nBy platform")
        for r in s["platforms"]:
            print(f"  {r['name']:<16} {r['posts']:>3} post(s)  {compact(r['views']):>7} views"
                  f"  {compact(r['avg_views']):>6} avg  {compact(r['likes']):>6} likes")
        if s["top"]:
            print("\nTop posts")
            for p in s["top"]:
                print(f"  {p['post_id'][:8]}  {compact(p['totals']['views']):>7} views  {short(p['caption'], 50)}")
        if s["by_hour"] and s["enough_data"]:
            best = max(s["by_hour"], key=lambda h: h["avg_views"])
            print(f"\nYour posts do best around {best['hour']:02d}:00 ({compact(best['avg_views'])} views on average)")
        if s["hashtags"] and s["enough_data"]:
            print("Best hashtags: " + ", ".join(f"{h['tag']} ({compact(h['avg_views'])})" for h in s["hashtags"][:5]))
    if s["streak"] > 1:
        print(f"\n🔥 {s['streak']}-day posting streak")
    if s["upcoming"]:
        print(f"\nComing up ({len(s['upcoming'])})")
        for j in s["upcoming"][:8]:
            print(f"  {when(j['run_at'], zone(settings)):<20} {j['label']:<22} {j['caption']}")
    if s["refreshed_at"]:
        print(f"\nNumbers last fetched {when(s['refreshed_at'], zone(settings))}")
    return 0


def cmd_clean(args: argparse.Namespace, settings: Settings) -> int:
    from .tracker import Tracker, cleanup

    days = settings.keep_files_days if args.days is None else args.days
    if days <= 0:
        print("Nothing to do: keeping files is set to forever (VAUTO_KEEP_FILES_DAYS=0). Use --days N.")
        return 0
    freed = cleanup(settings, Tracker.open(settings), days=days, dry_run=args.dry_run)
    verb = "Would delete" if args.dry_run else "Deleted"
    print(f"{verb} {freed['files']} file(s) older than {days} days, {freed['bytes'] / 1e6:.0f} MB. "
          "Thumbnails, subtitles and anything still queued were kept.")
    return 0


def cmd_export(args: argparse.Namespace, settings: Settings) -> int:
    from .tracker import Tracker

    if not settings.db_path.is_file():
        print("No posts yet.")
        return 0
    args.output.write_text(Tracker.open(settings).export_csv(settings), encoding="utf-8")
    print(f"Saved {args.output}")
    return 0


def _platform_detail(key: str, settings: Settings) -> int:
    import textwrap

    from .config import platform_guide

    spec = platform_spec(key)
    guide = platform_guide(key)
    colour = use_colour()
    wrap = lambda text, indent="  ": textwrap.fill(" ".join(str(text).split()), 88, initial_indent=indent,  # noqa: E731
                                                   subsequent_indent=indent)
    print(paint(spec["name"], "ok", colour) + f"  ({', '.join(spec.get('regions', []))})")
    if guide.get("summary"):
        print(wrap(guide["summary"]))
    for label, field in (("How posts get seen", "how_it_works"), ("Length that works", "length"),
                         ("When to post", "best_times"), ("Earning", "earn")):
        if guide.get(field):
            print(f"\n{label}\n{wrap(guide[field])}")
    if guide.get("tips"):
        print("\nTips")
        for tip in guide["tips"]:
            print(textwrap.fill(tip, 88, initial_indent="  - ", subsequent_indent="    "))
    status = spec.get("status", "planned")
    print("\nIn vauto")
    if status == "planned":
        print(f"  No posting route yet ({spec.get('route', 'researched only')}).")
        return 0
    ready, detail = platform_ready(settings, key)
    video, caption = spec.get("video") or {}, spec.get("caption") or {}
    print(f"  Route: {settings.backends.get(key)} ({status}); {'ready' if ready else 'needs setup'}: {detail}")
    if video.get("max_s"):
        print(f"  Video: {video.get('min_s', 0)}–{video['max_s']} s, up to {video.get('max_mb', '?')} MB")
    if caption.get("max_chars"):
        tags = f", {caption['max_hashtags']} hashtags max" if caption.get("max_hashtags") else ""
        print(f"  Caption: {caption['max_chars']} characters{tags}")
    if spec.get("rate_limit"):
        print(f"  Limits: {spec['rate_limit']}")
    return 0


def cmd_platforms(args: argparse.Namespace, settings: Settings) -> int:
    if args.name:
        return _platform_detail(args.name.strip().lower(), settings)
    colour = use_colour()
    for key, spec in load_platforms().items():
        if args.tier and spec.get("tier") != args.tier:
            continue
        status = spec.get("status", "planned")
        if status == "planned":
            if args.ready:
                continue
            print(paint(f"T{spec.get('tier')}  {key:<18} planned   {spec.get('route', '')}", "off", colour))
            continue
        ready, detail = platform_ready(settings, key)
        if args.ready and not ready:
            continue
        mark = paint("ready" if ready else "setup", "ok" if ready else "warn", colour)
        print(f"T{spec.get('tier')}  {key:<18} {mark:<8}  {settings.backends.get(key):<9} {detail}")
    print("\nHow a platform works and what to post there: vauto platforms NAME (e.g. vauto platforms tiktok)")
    return 0


def cmd_accounts(args: argparse.Namespace, settings: Settings) -> int:
    accounts = ZernioPublisher(settings).list_accounts()
    if not accounts:
        print("No accounts connected in Zernio yet.")
    names = {platform_spec(p).get("zernio_platform"): p for p in settings.backends if platform_spec(p).get("zernio_platform")}
    for acc in accounts:
        acc_id = acc.get("_id") or acc.get("id") or acc.get("accountId")
        platform = acc.get("platform", "?")
        ours = names.get(platform, platform)
        name = acc.get("username") or acc.get("displayName") or ""
        print(f"{platform:<12} {acc_id}  {name}   ZERNIO_ACCOUNT_{ours.upper()}={acc_id}")
    return 0


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    checks = run_checks(settings, online=args.online)
    if args.json:
        print(json.dumps([c.to_dict() for c in checks], indent=2))
    else:
        print(format_checks(checks, use_colour()))
    return 1 if any(c.status == "fail" and c.area == "tools" for c in checks) else 0


def cmd_best_time(args: argparse.Namespace, settings: Settings) -> int:
    _, note = service.resolve_publish_at(settings, "best", args.platforms.split(","))
    print(note)
    return 0


def cmd_auth(args: argparse.Namespace, settings: Settings) -> int:
    if args.auth_command == "meta":
        info = auth.meta_setup(settings, args.user_token)
        pages = info["pages"]
        if not pages:
            raise VautoError("No Facebook Pages found for this token. Give it pages_show_list and your Page.")
        page = next((p for p in pages if p["page_id"] == args.page), pages[0]) if args.page else pages[0]
        print("Long-lived user token" + (" (exchanged)" if info["exchanged"] else
                                         " (set META_APP_ID/META_APP_SECRET to exchange it)"))
        for p in pages:
            ig = f"Instagram @{p['ig_username']} ({p['ig_user_id']})" if p["ig_user_id"] else "no Instagram linked"
            print(f"  Page {p['page_name']} ({p['page_id']}): {ig}")
        values = {"FB_PAGE_ID": page["page_id"], "FB_PAGE_ACCESS_TOKEN": page["page_token"]}
        if page["ig_user_id"]:
            values.update(IG_USER_ID=page["ig_user_id"], IG_ACCESS_TOKEN=page["page_token"],
                          META_GRAPH_HOST="graph.facebook.com")
        if args.write:
            path = settings.dotenv_path or Path.cwd() / ".env"
            changed = update_env(path, values)
            print(f"Saved {', '.join(changed) or 'nothing new'} to {path}.")
        if info["exchanged"]:
            print("These Page tokens do not expire.")
        else:
            print("Warning: without META_APP_ID and META_APP_SECRET these tokens expire in about an hour. "
                  "Add both (from your Meta app's Settings > Basic) and run this again for tokens that don't expire.")
        if not args.write:
            print("Add these to .env (or rerun with --write):")
            for key, value in values.items():
                print(f"{key}={value}")
        return 0
    if args.auth_command == "tiktok":
        if not args.code:
            print("1. Open this link, log in to TikTok and approve:")
            print(auth.tiktok_authorize_url(settings))
            print("2. Copy the URL TikTok sends you to, then run:")
            print('   vauto auth tiktok --code "<that URL>"')
            return 0
        record = auth.tiktok_exchange(settings, args.code)
        print(f"TikTok connected (drafts). Token refreshes automatically until {record.get('refresh_expires_at')}.")
        return 0
    if args.auth_command == "refresh":
        for name, fn in (("instagram", auth.instagram_token), ("threads", auth.threads_token)):
            try:
                print(f"{name}: {'ok' if fn(settings) else 'not configured'}")
            except VautoError as exc:
                print(f"{name}: {exc}")
        try:
            auth.tiktok_token(settings)
            print("tiktok: ok")
        except VautoError as exc:
            print(f"tiktok: {exc}")
        return 0
    rows = auth.token_status(settings)
    if not rows:
        print("No stored tokens yet (tokens in .env are used as they are).")
    for row in rows:
        print(f"{row['name']:<10} refreshed {row['refreshed_at']}  expires {row['expires_at']}")
    return 0


def cmd_browser(args: argparse.Namespace, settings: Settings) -> int:
    from .publishers import browser

    if args.browser_command == "login":
        path = browser.login(settings, args.platform)
        print(f"Saved the {args.platform} login in {path}")
        return 0
    ok = browser.check(settings, args.platform)
    print(f"{args.platform}: {'logged in, upload page reachable' if ok else 'not logged in or the page changed'}")
    return 0 if ok else 1


def cmd_bot(args: argparse.Namespace, settings: Settings) -> int:
    from .bot import run_bot

    run_bot(settings)
    return 0


def _lan_addresses() -> list[str]:
    import socket

    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent; this picks the outward interface
            found.append(s.getsockname()[0])
    except OSError:
        pass
    return [a for a in found if not a.startswith("127.")]


def _tailscale_name() -> str | None:
    import shutil
    import subprocess

    if not shutil.which("tailscale"):
        return None
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        name = json.loads(out.stdout).get("Self", {}).get("DNSName", "")
        return name.rstrip(".") or None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def cmd_phone(args: argparse.Namespace, settings: Settings) -> int:
    import base64

    password = settings.web_password
    public = (settings.env.get("VAUTO_PUBLIC_URL") or "").rstrip("/")
    ts = _tailscale_name()
    print("Use vauto from your phone\n")
    print("1. Keep vauto running on a computer that stays on: `docker compose up -d` (or `vauto web` + `vauto worker`).")
    if not password:
        print("2. Set VAUTO_WEB_PASSWORD first. The app will not open to your phone without it.")
    else:
        print("2. Password: set.")
    print("3. Give it an https address your phone can reach (needed to install the app and share into it):")
    if ts:
        print(f"   Tailscale is installed. Run:  tailscale serve --bg {args.port}")
        print(f"   Then set VAUTO_PUBLIC_URL=https://{ts}")
    else:
        print("   Install Tailscale on this computer and your phone (free), then run:")
        print(f"   tailscale serve --bg {args.port}   and set VAUTO_PUBLIC_URL to the https address it prints.")
    for ip in _lan_addresses():
        print(f"   On the same Wi-Fi you can also browse to http://{ip}:{args.port} (no install or sharing over http).")
    base = public or (f"https://{ts}" if ts else "https://<your-address>")
    print(f"\n4. Open {base} on your phone.")
    print("   Android (Chrome): menu > Install app. Then share any video to 'vauto' from Gallery or CapCut.")
    print("   iPhone (Safari): Share > Add to Home Screen. For the share sheet, make a Shortcut:")
    print("     - Shortcut settings: Show in Share Sheet, accepts Media")
    print(f"     - Get Contents of URL: {base}/share?format=json, Method POST, Request Body: Form,")
    print("       field 'files' (File) = Shortcut Input, optional field 'text' = Ask for Input")
    if password:
        token = base64.b64encode(f"vauto:{password}".encode()).decode()
        print(f"       Header  Authorization = Basic {token}")
    else:
        print("       Header  Authorization = Basic <run `vauto phone` again after setting the password>")
    print("     - Get Dictionary Value 'open_url' from Contents of URL, then Open URLs")
    print("\n5. Or use Telegram on either phone: share the video to your vauto bot (`vauto bot`).")
    print("   Videos over 20 MB need the big-files server: README > Use it from your phone.")
    return 0


def cmd_web(args: argparse.Namespace, settings: Settings) -> int:
    from .web.app import run

    run(settings, args.host, args.port)
    return 0


def cmd_probe(args: argparse.Namespace, settings: Settings) -> int:
    for path in args.files:
        print(json.dumps(probe(path).__dict__, indent=2))
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
    "post": cmd_post, "worker": cmd_worker, "jobs": cmd_jobs, "trials": cmd_trials, "platforms": cmd_platforms,
    "posts": cmd_posts, "stats": cmd_stats, "export": cmd_export, "clean": cmd_clean,
    "accounts": cmd_accounts, "doctor": cmd_doctor, "best-time": cmd_best_time, "auth": cmd_auth,
    "browser": cmd_browser, "bot": cmd_bot, "web": cmd_web, "phone": cmd_phone, "probe": cmd_probe, "variant": cmd_variant,
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
