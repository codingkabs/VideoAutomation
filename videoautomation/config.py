"""Settings from environment / .env, and platform specs from platforms.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import ConfigError

PLATFORMS_FILE = Path(__file__).parent / "config" / "platforms.yaml"
GUIDE_FILE = PLATFORMS_FILE.with_name("platform_guide.yaml")
TIER1 = ("instagram", "facebook", "tiktok", "youtube", "snapchat")
POSTABLE_STATUSES = ("implemented", "beta", "handoff")
DEFAULT_BEST_TIMES = "mon-fri 07:30,12:30,18:00,20:30; sat-sun 10:00,19:30"


# --------------------------------------------------------------------------- .env


def parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser (comments, quotes and `export` supported)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = value.replace('\\"', '"').replace("\\\\", "\\")
        values[key] = value
    return values


def load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Merge a .env file into ``env``. Values already in ``env`` win."""
    for key, value in parse_dotenv(path).items():
        env.setdefault(key, value)


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _range(value: str | None, default: tuple[int, int]) -> tuple[int, int]:
    if not value:
        return default
    parts = value.replace(" ", "").split("-")
    try:
        lo = int(parts[0])
        hi = int(parts[1]) if len(parts) > 1 else lo
    except ValueError as exc:
        raise ConfigError(f"Expected a minute range like 60-120, got {value!r}") from exc
    if lo < 0 or hi < lo:
        raise ConfigError(f"Invalid minute range {value!r}")
    return lo, hi


def _ids(value: str | None) -> list[int]:
    out = []
    for part in (value or "").replace(" ", "").split(","):
        if part:
            try:
                out.append(int(part))
            except ValueError as exc:
                raise ConfigError(f"Expected numeric Telegram ids, got {part!r}") from exc
    return out


# Settings each direct route needs; used to fall back to Zernio when they're missing.
DIRECT_KEYS = {
    ("meta", "instagram"): ("IG_USER_ID", "IG_ACCESS_TOKEN"),
    ("meta", "facebook"): ("FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN"),
    ("threads", None): ("THREADS_USER_ID", "THREADS_ACCESS_TOKEN"),
    ("bluesky", None): ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD"),
    ("telegram", None): ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID"),
    ("mastodon", None): ("MASTODON_INSTANCE", "MASTODON_ACCESS_TOKEN"),
}


def _direct_ready(backend: str, platform: str, get) -> bool:
    keys = DIRECT_KEYS.get((backend, platform)) or DIRECT_KEYS.get((backend, None))
    if keys is None:
        return True  # routes without settings here (hand-off, browser, TikTok drafts) are left alone
    return all(get(k) for k in keys)


# ------------------------------------------------------------------------ settings


@dataclass
class Settings:
    home: Path
    default_platforms: list[str]
    backends: dict[str, str]
    storage: str  # auto | s3 | zernio | none
    env: dict[str, str] = field(default_factory=dict, repr=False)
    dotenv_path: Path | None = None

    # Meta (Instagram + Facebook direct)
    meta_graph_host: str = "graph.facebook.com"
    meta_graph_version: str = "v23.0"
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    ig_user_id: str | None = None
    ig_access_token: str | None = None
    fb_page_id: str | None = None
    fb_page_token: str | None = None
    threads_user_id: str | None = None
    threads_access_token: str | None = None

    # Zernio unified API
    zernio_api_key: str | None = None
    zernio_base_url: str = "https://zernio.com/api/v1"
    zernio_profile_id: str | None = None
    zernio_accounts: dict[str, str] = field(default_factory=dict)

    # TikTok direct (drafts; no audit needed)
    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None
    tiktok_redirect_uri: str | None = None

    # Other direct platforms
    bluesky_handle: str | None = None
    bluesky_app_password: str | None = None
    bluesky_pds: str = "https://bsky.social"
    telegram_bot_token: str | None = None
    telegram_channel_id: str | None = None
    telegram_owner_chat_id: str | None = None
    telegram_allowed_user_ids: list[int] = field(default_factory=list)
    telegram_api_base: str = "https://api.telegram.org"
    mastodon_instance: str | None = None
    mastodon_access_token: str | None = None
    reddit_subreddit: str | None = None
    reddit_flair_id: str | None = None
    pinterest_board_id: str | None = None
    pinterest_link: str | None = None

    # Browser automation and social-auto-upload
    browser_path: str | None = None
    browser_headless: bool = True
    browser_fallback_handoff: bool = True
    sau_command: str = "sau"
    sau_account: str = "default"
    bilibili_tid: str = "160"

    # S3 / Cloudflare R2
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_public_base_url: str | None = None
    s3_prefix: str = "vauto"
    url_ttl_hours: int = 48

    # Behaviour
    timezone: str = "Europe/London"
    best_times: str = DEFAULT_BEST_TIMES
    trial_default: bool = False
    trial_delay_minutes: tuple[int, int] = (60, 120)
    trial_graduation: str = "MANUAL"
    trial_backend: str = "auto"  # auto | meta | zernio
    trial_report_hours: int = 72
    tiktok_privacy_level: str = "PUBLIC_TO_EVERYONE"
    youtube_visibility: str = "public"
    youtube_add_shorts_tag: bool = True
    snapchat_content_type: str = "spotlight"
    font_path: str | None = None

    # Subtitles
    subtitle_model: str = "small"
    subtitle_language: str | None = None
    subtitle_style: str = "bold"

    # Optional Claude caption rewriting
    anthropic_api_key: str | None = None
    caption_model: str = "claude-opus-5"

    # Web UI
    web_password: str | None = None

    # My posts: numbers refresh and summaries
    stats_refresh_hours: int = 6
    digest: str = "weekly"  # weekly | daily | off
    keep_files_days: int = 14  # delete rendered videos and uploads older than this (0 = keep)
    drafts_default: bool = False  # "Save as drafts" ticked by default

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, dotenv: Path | None = None) -> "Settings":
        merged: dict[str, str] = dict(os.environ if env is None else env)
        if dotenv is None and env is None:
            dotenv = Path.cwd() / ".env"
        if dotenv is not None:
            load_dotenv(dotenv, merged)
        g = merged.get

        home = Path(g("VAUTO_HOME") or Path.home() / ".vauto").expanduser()
        platforms = [p.strip().lower() for p in (g("VAUTO_PLATFORMS") or ",".join(TIER1)).split(",") if p.strip()]

        backends: dict[str, str] = {}
        zernio_accounts: dict[str, str] = {}
        for name in postable_platforms():
            spec = platform_spec(name)
            key = name.upper()
            explicit = g(f"VAUTO_BACKEND_{key}")
            choice = (explicit or spec["default_backend"]).lower()
            if (not explicit and choice != "zernio" and "zernio" in spec["backends"] and g("ZERNIO_API_KEY")
                    and g(f"ZERNIO_ACCOUNT_{key}") and not _direct_ready(choice, name, g)):
                # Connected in Zernio but the direct route isn't set up: use Zernio.
                choice = "zernio"
            if choice not in spec["backends"]:
                raise ConfigError(
                    f"VAUTO_BACKEND_{key}={choice} is not supported; use one of {', '.join(spec['backends'])}"
                )
            backends[name] = choice
            if g(f"ZERNIO_ACCOUNT_{key}"):
                zernio_accounts[name] = g(f"ZERNIO_ACCOUNT_{key}", "")

        graduation = (g("VAUTO_TRIAL_GRADUATION") or "MANUAL").upper()
        if graduation not in ("MANUAL", "SS_PERFORMANCE"):
            raise ConfigError("VAUTO_TRIAL_GRADUATION must be MANUAL or SS_PERFORMANCE")
        digest = (g("VAUTO_DIGEST") or "weekly").lower()
        if digest not in ("weekly", "daily", "off"):
            raise ConfigError("VAUTO_DIGEST must be weekly, daily or off")
        try:
            stats_hours = int(g("VAUTO_STATS_REFRESH_HOURS") or 6)
        except ValueError as exc:
            raise ConfigError("VAUTO_STATS_REFRESH_HOURS must be a whole number of hours (0 turns it off)") from exc
        try:
            keep_days = int(g("VAUTO_KEEP_FILES_DAYS") or 14)
        except ValueError as exc:
            raise ConfigError("VAUTO_KEEP_FILES_DAYS must be a whole number of days (0 keeps everything)") from exc
        style = (g("VAUTO_SUBTITLE_STYLE") or "bold").lower()
        if style not in ("bold", "clean"):
            raise ConfigError("VAUTO_SUBTITLE_STYLE must be bold or clean")

        def opt(name: str) -> str | None:
            return g(name) or None

        return cls(
            home=home,
            default_platforms=platforms,
            backends=backends,
            storage=(g("VAUTO_STORAGE") or "auto").lower(),
            env=merged,
            dotenv_path=dotenv,
            meta_graph_host=g("META_GRAPH_HOST") or "graph.facebook.com",
            meta_graph_version=g("META_GRAPH_VERSION") or "v23.0",
            meta_app_id=opt("META_APP_ID"),
            meta_app_secret=opt("META_APP_SECRET"),
            ig_user_id=opt("IG_USER_ID"),
            ig_access_token=opt("IG_ACCESS_TOKEN"),
            fb_page_id=opt("FB_PAGE_ID"),
            fb_page_token=opt("FB_PAGE_ACCESS_TOKEN"),
            threads_user_id=opt("THREADS_USER_ID"),
            threads_access_token=opt("THREADS_ACCESS_TOKEN"),
            zernio_api_key=opt("ZERNIO_API_KEY"),
            zernio_base_url=(g("ZERNIO_BASE_URL") or "https://zernio.com/api/v1").rstrip("/"),
            zernio_profile_id=opt("ZERNIO_PROFILE_ID"),
            zernio_accounts=zernio_accounts,
            tiktok_client_key=opt("TIKTOK_CLIENT_KEY"),
            tiktok_client_secret=opt("TIKTOK_CLIENT_SECRET"),
            tiktok_redirect_uri=opt("TIKTOK_REDIRECT_URI"),
            bluesky_handle=opt("BLUESKY_HANDLE"),
            bluesky_app_password=opt("BLUESKY_APP_PASSWORD"),
            bluesky_pds=(g("BLUESKY_PDS") or "https://bsky.social").rstrip("/"),
            telegram_bot_token=opt("TELEGRAM_BOT_TOKEN"),
            telegram_channel_id=opt("TELEGRAM_CHANNEL_ID"),
            telegram_owner_chat_id=opt("TELEGRAM_OWNER_CHAT_ID"),
            telegram_allowed_user_ids=_ids(g("TELEGRAM_ALLOWED_USER_IDS")),
            telegram_api_base=(g("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/"),
            mastodon_instance=(g("MASTODON_INSTANCE") or "").rstrip("/") or None,
            mastodon_access_token=opt("MASTODON_ACCESS_TOKEN"),
            reddit_subreddit=(g("REDDIT_SUBREDDIT") or "").removeprefix("r/") or None,
            reddit_flair_id=opt("REDDIT_FLAIR_ID"),
            pinterest_board_id=opt("PINTEREST_BOARD_ID"),
            pinterest_link=opt("PINTEREST_LINK"),
            browser_path=opt("VAUTO_BROWSER_PATH"),
            browser_headless=_bool(g("VAUTO_BROWSER_HEADLESS"), True),
            browser_fallback_handoff=_bool(g("VAUTO_BROWSER_FALLBACK_HANDOFF"), True),
            sau_command=g("SAU_COMMAND") or "sau",
            sau_account=g("SAU_ACCOUNT") or "default",
            bilibili_tid=g("BILIBILI_TID") or "160",
            s3_bucket=opt("S3_BUCKET"),
            s3_endpoint_url=opt("S3_ENDPOINT_URL"),
            s3_region=opt("S3_REGION"),
            s3_access_key=opt("S3_ACCESS_KEY_ID"),
            s3_secret_key=opt("S3_SECRET_ACCESS_KEY"),
            s3_public_base_url=(g("S3_PUBLIC_BASE_URL") or "").rstrip("/") or None,
            s3_prefix=g("S3_PREFIX") or "vauto",
            url_ttl_hours=int(g("VAUTO_URL_TTL_HOURS") or 48),
            timezone=g("VAUTO_TIMEZONE") or "Europe/London",
            best_times=g("VAUTO_BEST_TIMES") or DEFAULT_BEST_TIMES,
            trial_default=_bool(g("VAUTO_TRIAL_DEFAULT")),
            trial_delay_minutes=_range(g("VAUTO_TRIAL_DELAY"), (60, 120)),
            trial_graduation=graduation,
            trial_backend=(g("VAUTO_TRIAL_BACKEND") or "auto").lower(),
            trial_report_hours=int(g("VAUTO_TRIAL_REPORT_HOURS") or 72),
            tiktok_privacy_level=g("TIKTOK_PRIVACY_LEVEL") or "PUBLIC_TO_EVERYONE",
            youtube_visibility=g("YOUTUBE_VISIBILITY") or "public",
            youtube_add_shorts_tag=_bool(g("YOUTUBE_ADD_SHORTS_TAG"), True),
            snapchat_content_type=g("SNAPCHAT_CONTENT_TYPE") or "spotlight",
            font_path=opt("VAUTO_FONT"),
            subtitle_model=g("VAUTO_SUBTITLE_MODEL") or "small",
            subtitle_language=opt("VAUTO_SUBTITLE_LANGUAGE"),
            subtitle_style=style,
            anthropic_api_key=opt("ANTHROPIC_API_KEY"),
            caption_model=g("VAUTO_CAPTION_MODEL") or "claude-opus-5",
            web_password=opt("VAUTO_WEB_PASSWORD"),
            stats_refresh_hours=max(0, stats_hours),
            digest=digest,
            keep_files_days=max(0, keep_days),
            drafts_default=_bool(g("VAUTO_DRAFTS_DEFAULT")),
        )

    def trial_backend_for(self) -> str:
        """Backend for delayed Trial Reels. ``auto`` prefers Zernio when an Instagram
        account is connected there, because Zernio schedules server-side and nothing
        has to keep running locally until the post time."""
        if self.trial_backend in ("meta", "zernio"):
            return self.trial_backend
        if self.trial_backend != "auto":
            raise ConfigError("VAUTO_TRIAL_BACKEND must be auto, meta or zernio")
        if self.zernio_api_key and self.zernio_accounts.get("instagram"):
            return "zernio"
        return self.backends["instagram"]

    @property
    def db_path(self) -> Path:
        return self.home / "jobs.db"

    @property
    def renders_dir(self) -> Path:
        return self.home / "renders"

    @property
    def outbox_dir(self) -> Path:
        return self.home / "outbox"

    @property
    def tokens_path(self) -> Path:
        return self.home / "tokens.json"


# ----------------------------------------------------------------- platform specs


@lru_cache(maxsize=1)
def load_platforms() -> dict[str, dict[str, Any]]:
    with PLATFORMS_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["platforms"]


@lru_cache(maxsize=1)
def load_guide() -> dict[str, dict[str, Any]]:
    """How each platform works, for creators (config/platform_guide.yaml)."""
    with GUIDE_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)["guide"]


def platform_guide(name: str) -> dict[str, Any]:
    return load_guide().get(name, {})


def platform_spec(name: str) -> dict[str, Any]:
    platforms = load_platforms()
    if name not in platforms:
        raise ConfigError(f"Unknown platform {name!r}. Run `vauto platforms` to list them.")
    return platforms[name]


def postable_platforms() -> list[str]:
    """Platforms vauto can post to (official API, browser/sau beta, or hand-off)."""
    return [k for k, v in load_platforms().items() if v.get("status") in POSTABLE_STATUSES]


def implemented_platforms() -> list[str]:
    """Kept for compatibility: every platform vauto can post to."""
    return postable_platforms()
