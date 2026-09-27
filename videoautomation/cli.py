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
from .ffmpeg import probe
from .media.normalize import FIT_MODES, normalize_video
from .media.variants import VariantOptions, render_variant
from .publishers import ZernioPublisher
from .report import format_checks, format_results, paint, results_json, summary_line, use_colour, when
from .scheduler import JobStore, worker_loop


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

    trials = sub.add_parser("trials", help="compare Trial Reels with their main Reels")
    trials.add_argument("--limit", type=int, default=5)

    plats = sub.add_parser("platforms", help="list every platform, its route and whether it is set up")
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
        tiktok_draft=args.tiktok_draft, ig_story=args.ig_story, fit=args.fit, allow_trim=args.trim,
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
        print(format_results(results, plan.notes, colour))
        print(f"\n{summary_line(results)}")
    if not req.dry_run:
        service.notify_results(settings, results, f"vauto post {plan.post_id}")
    return 1 if any(r.status == "failed" for r in results) else 0


def cmd_worker(args: argparse.Namespace, settings: Settings) -> int:
    store = JobStore(settings.db_path)
    publisher_for = service.publisher_factory(settings)
    colour = use_colour()
    failed = False
    if not args.once:
        print(f"vauto worker: checking every {args.interval:g}s (Ctrl+C to stop)")
    try:
        for result in worker_loop(store, publisher_for, interval=args.interval, once=args.once):
            failed = failed or result.status == "failed"
            print(format_results([result], colour=colour), flush=True)
            if result.status != "queued":
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
        print(f"{row.idem_key[:8]}  {when(row.run_at):<16} {row.job.label:<22} {status} {detail}")
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


def cmd_platforms(args: argparse.Namespace, settings: Settings) -> int:
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
            print(f"Saved {', '.join(changed) or 'nothing new'} to {path}. Page tokens do not expire.")
        else:
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
    "accounts": cmd_accounts, "doctor": cmd_doctor, "best-time": cmd_best_time, "auth": cmd_auth,
    "browser": cmd_browser, "bot": cmd_bot, "web": cmd_web, "probe": cmd_probe, "variant": cmd_variant,
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
