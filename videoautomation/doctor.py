"""Setup checks: what is ready, what is missing, and how to fix it."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import timedelta

import requests

from . import auth
from .config import Settings, platform_spec, postable_platforms
from .scheduler import JobStore, parse_iso, utcnow


@dataclass
class Check:
    area: str
    name: str
    status: str  # ok | warn | fail | off
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def platform_ready(settings: Settings, platform: str) -> tuple[bool, str]:
    """Is this platform configured for its chosen backend? (No network calls.)"""
    backend = settings.backends.get(platform)
    s = settings
    if backend is None:
        return False, "no posting route"
    if backend == "meta" and platform == "instagram":
        ok = bool(s.ig_user_id and (s.ig_access_token or auth.store_for(s).get("instagram")))
        return ok, "Meta direct" if ok else "set IG_USER_ID and IG_ACCESS_TOKEN (vauto auth meta)"
    if backend == "meta" and platform == "facebook":
        ok = bool(s.fb_page_id and s.fb_page_token)
        return ok, "Meta direct" if ok else "set FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN (vauto auth meta)"
    if backend == "zernio":
        if not s.zernio_api_key:
            return False, "set ZERNIO_API_KEY"
        if not s.zernio_accounts.get(platform):
            return False, f"set ZERNIO_ACCOUNT_{platform.upper()} (vauto accounts)"
        return True, "Zernio"
    if backend == "threads":
        ok = bool(s.threads_user_id and s.threads_access_token)
        return ok, "Threads API" if ok else "set THREADS_USER_ID and THREADS_ACCESS_TOKEN"
    if backend == "bluesky":
        ok = bool(s.bluesky_handle and s.bluesky_app_password)
        return ok, "Bluesky" if ok else "set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD"
    if backend == "telegram":
        ok = bool(s.telegram_bot_token and s.telegram_channel_id)
        return ok, "Telegram bot" if ok else "set TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID"
    if backend == "mastodon":
        ok = bool(s.mastodon_instance and s.mastodon_access_token)
        return ok, "Mastodon" if ok else "set MASTODON_INSTANCE and MASTODON_ACCESS_TOKEN"
    if backend == "tiktok":
        ok = bool(auth.store_for(s).get("tiktok").get("access_token"))
        return ok, "TikTok drafts" if ok else "run `vauto auth tiktok`"
    if backend == "handoff":
        if s.telegram_bot_token and s.telegram_owner_chat_id:
            return True, "hand-off to your phone"
        return True, "hand-off to the outbox folder (add Telegram to get it on your phone)"
    if backend == "browser":
        if not _has("playwright"):
            return False, "pip install 'vauto[browser]'"
        profile = s.home / "browser" / platform
        if not profile.is_dir():
            return False, f"run `vauto browser login {platform}`"
        return True, "browser automation (beta)"
    if backend == "sau":
        ok = shutil.which(s.sau_command) is not None
        return ok, "social-auto-upload (beta)" if ok else "install social-auto-upload (README > China)"
    return False, f"unknown backend {backend}"


def _tool_checks(settings: Settings) -> list[Check]:
    out = []
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path:
            version = subprocess.run([tool, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
            out.append(Check("tools", tool, "ok", version[:60]))
        else:
            out.append(Check("tools", tool, "fail", "not installed", "install ffmpeg (brew/apt/winget)"))
    if shutil.which("ffmpeg"):
        filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        for name, use in (("drawtext", "Trial Reel hook text"), ("ass", "burned-in subtitles")):
            ok = f" {name} " in filters
            out.append(Check("tools", f"ffmpeg {name}", "ok" if ok else "warn",
                             use if ok else f"missing: {use} will not work",
                             "" if ok else "install an ffmpeg build with libfreetype/libass"))
    from .media.variants import find_font

    try:
        font = find_font(settings.font_path)
    except Exception:
        font = None
    out.append(Check("tools", "font", "ok" if font else "warn", font or "no bold font found",
                     "" if font else "set VAUTO_FONT to a .ttf file"))
    extras = [
        ("anthropic", "caption rewriting (--rewrite-captions)", "claude"),
        ("faster_whisper", "automatic subtitles (--subtitles auto)", "subtitles"),
        ("playwright", "browser automation platforms", "browser"),
        ("flask", "web app (vauto web)", "web"),
        ("boto3", "R2/S3 storage", "s3"),
    ]
    for module, use, extra in extras:
        ok = _has(module)
        out.append(Check("optional", module, "ok" if ok else "off", use,
                         "" if ok else f"pip install 'vauto[{extra}]'"))
    return out


def run_checks(settings: Settings, online: bool = False) -> list[Check]:
    checks = _tool_checks(settings)

    wanted = list(dict.fromkeys(settings.default_platforms))
    for platform in postable_platforms():
        if platform in wanted:
            continue
        ready, _ = platform_ready(settings, platform)
        backend = settings.backends.get(platform)
        if ready and backend not in ("handoff",):
            wanted.append(platform)  # configured extras show up too
    for platform in wanted:
        try:
            spec = platform_spec(platform)
        except Exception:
            checks.append(Check("platforms", platform, "fail", "unknown platform in VAUTO_PLATFORMS"))
            continue
        ready, detail = platform_ready(settings, platform)
        status = "ok" if ready else "fail"
        if ready and spec.get("status") == "beta":
            status = "warn"
        checks.append(Check("platforms", spec["name"], status,
                            f"{settings.backends.get(platform)}: {detail}", "" if ready else detail))

    from .storage import make_storage

    try:
        storage = make_storage(settings)
        checks.append(Check("setup", "public media storage", "ok" if storage else "warn",
                            storage.name if storage else "none (needed for Instagram photos and Zernio posts)",
                            "" if storage else "set ZERNIO_API_KEY or S3_BUCKET"))
    except Exception as exc:
        checks.append(Check("setup", "public media storage", "fail", str(exc)))

    trial_backend = settings.trial_backend_for() if "instagram" in settings.backends else "n/a"
    local = trial_backend == "meta"
    checks.append(Check("setup", "Trial Reel scheduling", "warn" if local else "ok",
                        "local queue: `vauto worker` must be running at post time" if local
                        else f"{trial_backend}: scheduled server-side",
                        "connect Instagram in Zernio (ZERNIO_ACCOUNT_INSTAGRAM)" if local else ""))

    tg_ok = bool(settings.telegram_bot_token and settings.telegram_owner_chat_id)
    checks.append(Check("setup", "phone notifications", "ok" if tg_ok else "off",
                        "Telegram" if tg_ok else "off (hand-offs go to the outbox folder)",
                        "" if tg_ok else "set TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_CHAT_ID"))

    if settings.db_path.is_file():
        store = JobStore(settings.db_path)
        pending = store.rows(include_done=False, limit=500)
        overdue = [r for r in pending if r.status == "pending" and parse_iso(r.run_at) < utcnow() - timedelta(minutes=15)]
        status = "warn" if overdue else "ok"
        checks.append(Check("queue", "local queue", status,
                            f"{len(pending)} waiting, {len(overdue)} overdue",
                            "start `vauto worker` (or cron `vauto worker --once`)" if overdue else ""))

    for row in auth.token_status(settings):
        checks.append(Check("tokens", row["name"], "ok", f"refreshed {row['refreshed_at']}, expires {row['expires_at']}"))

    if online:
        checks += _online_checks(settings)
    return checks


def _online_checks(settings: Settings) -> list[Check]:
    out = []
    session = requests.Session()
    if settings.zernio_api_key:
        from .publishers.zernio import ZernioPublisher

        try:
            accounts = ZernioPublisher(settings, session=session).list_accounts()
            names = sorted({a.get("platform", "?") for a in accounts})
            out.append(Check("online", "Zernio", "ok", f"connected: {', '.join(names) or 'none'}"))
        except Exception as exc:
            out.append(Check("online", "Zernio", "fail", str(exc)[:200], "check ZERNIO_API_KEY / network"))
    token = auth.instagram_token(settings, session) if settings.ig_user_id else None
    if token:
        try:
            r = session.get(f"https://{settings.meta_graph_host}/{settings.meta_graph_version}/{settings.ig_user_id}",
                            params={"fields": "username", "access_token": token}, timeout=20)
            data = r.json()
            ok = r.status_code < 400
            out.append(Check("online", "Instagram token", "ok" if ok else "fail",
                             f"@{data.get('username')}" if ok else str(data.get("error", data))[:200]))
        except Exception as exc:
            out.append(Check("online", "Instagram token", "fail", str(exc)[:200]))
    if settings.telegram_bot_token:
        from .telegram_api import TelegramAPI

        try:
            me = TelegramAPI(settings.telegram_bot_token, settings.telegram_api_base, session).call("getMe")
            out.append(Check("online", "Telegram bot", "ok", f"@{me.get('username')}"))
        except Exception as exc:
            out.append(Check("online", "Telegram bot", "fail", str(exc)[:200]))
    return out
