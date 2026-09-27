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
TIER1 = ("instagram", "facebook", "tiktok", "youtube", "snapchat")


# --------------------------------------------------------------------------- .env


def load_dotenv(path: Path, env: dict[str, str]) -> None:
    """Minimal KEY=VALUE loader. Existing environment values win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
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


# ------------------------------------------------------------------------ settings


@dataclass
class Settings:
    home: Path
    default_platforms: list[str]
    backends: dict[str, str]
    storage: str  # auto | s3 | zernio | none

    # Meta (Instagram + Facebook direct)
    meta_graph_host: str = "graph.facebook.com"
    meta_graph_version: str = "v23.0"
    ig_user_id: str | None = None
    ig_access_token: str | None = None
    fb_page_id: str | None = None
    fb_page_token: str | None = None

    # Zernio unified API
    zernio_api_key: str | None = None
    zernio_base_url: str = "https://zernio.com/api/v1"
    zernio_accounts: dict[str, str] = field(default_factory=dict)

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
    trial_default: bool = False
    trial_delay_minutes: tuple[int, int] = (60, 120)
    trial_graduation: str = "MANUAL"
    trial_backend: str = "auto"  # auto | meta | zernio
    tiktok_privacy_level: str = "PUBLIC_TO_EVERYONE"
    youtube_visibility: str = "public"
    youtube_add_shorts_tag: bool = True
    snapchat_content_type: str = "spotlight"
    font_path: str | None = None

    # Optional Claude caption rewriting
    anthropic_api_key: str | None = None
    caption_model: str = "claude-opus-5"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, dotenv: Path | None = None) -> "Settings":
        merged: dict[str, str] = dict(os.environ if env is None else env)
        if dotenv is None and env is None:
            dotenv = Path.cwd() / ".env"
        if dotenv is not None:
            load_dotenv(dotenv, merged)
        g = merged.get

        home = Path(g("VAUTO_HOME") or Path.home() / ".vauto").expanduser()
        platforms = [p.strip() for p in (g("VAUTO_PLATFORMS") or ",".join(TIER1)).split(",") if p.strip()]

        backends = {}
        for name in TIER1:
            spec = platform_spec(name)
            choice = (g(f"VAUTO_BACKEND_{name.upper()}") or spec["default_backend"]).lower()
            if choice not in spec["backends"]:
                raise ConfigError(
                    f"VAUTO_BACKEND_{name.upper()}={choice} is not supported; use one of {', '.join(spec['backends'])}"
                )
            backends[name] = choice

        zernio_accounts = {
            name: g(f"ZERNIO_ACCOUNT_{name.upper()}", "")
            for name in TIER1
            if g(f"ZERNIO_ACCOUNT_{name.upper()}")
        }

        graduation = (g("VAUTO_TRIAL_GRADUATION") or "MANUAL").upper()
        if graduation not in ("MANUAL", "SS_PERFORMANCE"):
            raise ConfigError("VAUTO_TRIAL_GRADUATION must be MANUAL or SS_PERFORMANCE")

        return cls(
            home=home,
            default_platforms=platforms,
            backends=backends,
            storage=(g("VAUTO_STORAGE") or "auto").lower(),
            meta_graph_host=g("META_GRAPH_HOST") or "graph.facebook.com",
            meta_graph_version=g("META_GRAPH_VERSION") or "v23.0",
            ig_user_id=g("IG_USER_ID") or None,
            ig_access_token=g("IG_ACCESS_TOKEN") or None,
            fb_page_id=g("FB_PAGE_ID") or None,
            fb_page_token=g("FB_PAGE_ACCESS_TOKEN") or None,
            zernio_api_key=g("ZERNIO_API_KEY") or None,
            zernio_base_url=(g("ZERNIO_BASE_URL") or "https://zernio.com/api/v1").rstrip("/"),
            zernio_accounts=zernio_accounts,
            s3_bucket=g("S3_BUCKET") or None,
            s3_endpoint_url=g("S3_ENDPOINT_URL") or None,
            s3_region=g("S3_REGION") or None,
            s3_access_key=g("S3_ACCESS_KEY_ID") or None,
            s3_secret_key=g("S3_SECRET_ACCESS_KEY") or None,
            s3_public_base_url=(g("S3_PUBLIC_BASE_URL") or "").rstrip("/") or None,
            s3_prefix=g("S3_PREFIX") or "vauto",
            url_ttl_hours=int(g("VAUTO_URL_TTL_HOURS") or 48),
            trial_default=_bool(g("VAUTO_TRIAL_DEFAULT")),
            trial_delay_minutes=_range(g("VAUTO_TRIAL_DELAY"), (60, 120)),
            trial_graduation=graduation,
            trial_backend=(g("VAUTO_TRIAL_BACKEND") or "auto").lower(),
            tiktok_privacy_level=g("TIKTOK_PRIVACY_LEVEL") or "PUBLIC_TO_EVERYONE",
            youtube_visibility=g("YOUTUBE_VISIBILITY") or "public",
            youtube_add_shorts_tag=_bool(g("YOUTUBE_ADD_SHORTS_TAG"), True),
            snapchat_content_type=g("SNAPCHAT_CONTENT_TYPE") or "spotlight",
            font_path=g("VAUTO_FONT") or None,
            anthropic_api_key=g("ANTHROPIC_API_KEY") or None,
            caption_model=g("VAUTO_CAPTION_MODEL") or "claude-opus-5",
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


# ----------------------------------------------------------------- platform specs


@lru_cache(maxsize=1)
def load_platforms() -> dict[str, dict[str, Any]]:
    with PLATFORMS_FILE.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["platforms"]


def platform_spec(name: str) -> dict[str, Any]:
    platforms = load_platforms()
    if name not in platforms:
        raise ConfigError(f"Unknown platform {name!r}. Run `vauto platforms` to list them.")
    return platforms[name]


def implemented_platforms() -> list[str]:
    return [k for k, v in load_platforms().items() if v.get("status") == "implemented"]
