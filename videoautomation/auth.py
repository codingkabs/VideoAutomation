"""Tokens: a small local store, automatic refresh, and setup helpers.

- Instagram Login tokens (graph.instagram.com) and Threads tokens last 60 days
  and are refreshed automatically once they are a week old.
- Facebook Login users should post with a Page token, which never expires;
  `vauto auth meta` finds it for you.
- TikTok direct drafts use OAuth; access tokens last 24 h and are refreshed with
  the 365-day refresh token.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import Settings
from .errors import ConfigError, PublishError
from .http import call

REFRESH_AFTER = timedelta(days=7)
TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_SCOPES = "user.info.basic,video.upload"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class TokenStore:
    """JSON file of refreshed tokens. Newer than .env values, so it wins."""

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def get(self, name: str) -> dict[str, Any]:
        return self._load().get(name, {})

    def set(self, name: str, **values: Any) -> dict[str, Any]:
        data = self._load()
        record = {**data.get(name, {}), **values}
        data[name] = record
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        return record


def store_for(settings: Settings) -> TokenStore:
    return TokenStore(settings.tokens_path)


# ------------------------------------------------------- 60-day Meta tokens


def _refreshing_token(settings: Settings, name: str, env_token: str | None, refresh_url: str,
                      grant: str, session: requests.Session | None, now: datetime | None) -> str | None:
    store = store_for(settings)
    record = store.get(name)
    token = record.get("access_token") or env_token
    if not token:
        return None
    now = now or _now()
    refreshed = _parse(record.get("refreshed_at"))
    attempted = _parse(record.get("attempted_at"))
    due = refreshed is None or now - refreshed >= REFRESH_AFTER
    recently_tried = attempted is not None and now - attempted < timedelta(hours=12)
    if not due or (refreshed is None and recently_tried):
        return token
    session = session or requests.Session()
    try:
        data = call(session.get, refresh_url, params={"grant_type": grant, "access_token": token},
                    timeout=30, context=f"{name} token refresh")
    except PublishError:
        store.set(name, attempted_at=_iso(now))
        return token
    new = data.get("access_token")
    if not new:
        store.set(name, attempted_at=_iso(now))
        return token
    expires = now + timedelta(seconds=int(data.get("expires_in") or 5184000))
    store.set(name, access_token=new, refreshed_at=_iso(now), expires_at=_iso(expires), attempted_at=_iso(now))
    return new


def instagram_token(settings: Settings, session: requests.Session | None = None,
                    now: datetime | None = None) -> str | None:
    """Current Instagram token, refreshed when it uses Instagram Login."""
    if settings.meta_graph_host != "graph.instagram.com":
        return store_for(settings).get("instagram").get("access_token") or settings.ig_access_token
    return _refreshing_token(settings, "instagram", settings.ig_access_token,
                             "https://graph.instagram.com/refresh_access_token", "ig_refresh_token", session, now)


def threads_token(settings: Settings, session: requests.Session | None = None,
                  now: datetime | None = None) -> str | None:
    return _refreshing_token(settings, "threads", settings.threads_access_token,
                             "https://graph.threads.net/refresh_access_token", "th_refresh_token", session, now)


def token_status(settings: Settings) -> list[dict[str, Any]]:
    """What the store knows about each token, for `vauto doctor` and the web UI."""
    rows = []
    for name in ("instagram", "threads", "tiktok"):
        record = store_for(settings).get(name)
        if record:
            rows.append({"name": name, "refreshed_at": record.get("refreshed_at"),
                         "expires_at": record.get("expires_at") or record.get("refresh_expires_at")})
    return rows


# ------------------------------------------------------------- Meta setup


def meta_setup(settings: Settings, user_token: str, session: requests.Session | None = None) -> dict[str, Any]:
    """Turn a short-lived Graph API Explorer token into never-expiring Page tokens.

    Returns the long-lived user token and every Page you manage with its token
    and linked Instagram account.
    """
    session = session or requests.Session()
    base = f"https://graph.facebook.com/{settings.meta_graph_version}"
    long_lived = user_token
    if settings.meta_app_id and settings.meta_app_secret:
        data = call(session.get, f"{base}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret, "fb_exchange_token": user_token,
        }, timeout=30, context="Meta token exchange")
        long_lived = data.get("access_token") or user_token
    pages = call(session.get, f"{base}/me/accounts", params={
        "access_token": long_lived,
        "fields": "id,name,access_token,instagram_business_account{id,username}",
    }, timeout=30, context="Meta pages").get("data", [])
    return {
        "long_lived_user_token": long_lived,
        "exchanged": long_lived != user_token,
        "pages": [
            {
                "page_id": p.get("id"), "page_name": p.get("name"), "page_token": p.get("access_token"),
                "ig_user_id": (p.get("instagram_business_account") or {}).get("id"),
                "ig_username": (p.get("instagram_business_account") or {}).get("username"),
            }
            for p in pages
        ],
    }


# ------------------------------------------------------------------- TikTok


def _tiktok_app(settings: Settings) -> tuple[str, str, str]:
    if not (settings.tiktok_client_key and settings.tiktok_client_secret and settings.tiktok_redirect_uri):
        raise ConfigError("Set TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI (see README)")
    return settings.tiktok_client_key, settings.tiktok_client_secret, settings.tiktok_redirect_uri


def tiktok_authorize_url(settings: Settings, state: str | None = None) -> str:
    key, _, redirect = _tiktok_app(settings)
    state = state or secrets.token_urlsafe(12)
    store_for(settings).set("tiktok_oauth", state=state)
    return TIKTOK_AUTH_URL + "?" + urlencode({
        "client_key": key, "scope": TIKTOK_SCOPES, "response_type": "code",
        "redirect_uri": redirect, "state": state,
    })


def _save_tiktok(settings: Settings, data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not payload.get("access_token"):
        raise PublishError(f"TikTok did not return a token: {payload.get('error_description') or payload}")
    now = _now()
    return store_for(settings).set(
        "tiktok",
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        open_id=payload.get("open_id"),
        scope=payload.get("scope"),
        expires_at=_iso(now + timedelta(seconds=int(payload.get("expires_in") or 86400))),
        refresh_expires_at=_iso(now + timedelta(seconds=int(payload.get("refresh_expires_in") or 31536000))),
        refreshed_at=_iso(now),
    )


def tiktok_exchange(settings: Settings, code_or_url: str, session: requests.Session | None = None) -> dict[str, Any]:
    """Exchange the code TikTok sent to your redirect URL (paste the code or the whole URL)."""
    key, secret, redirect = _tiktok_app(settings)
    code = code_or_url.strip()
    if code.startswith("http"):
        query = parse_qs(urlparse(code).query)
        if "error" in query:
            raise PublishError(f"TikTok sign-in failed: {query.get('error_description', query['error'])[0]}")
        expected = store_for(settings).get("tiktok_oauth").get("state")
        if expected and query.get("state", [None])[0] != expected:
            raise PublishError("TikTok sign-in state did not match; run `vauto auth tiktok` again")
        code = query.get("code", [""])[0]
    if not code:
        raise ConfigError("No TikTok authorization code found")
    session = session or requests.Session()
    data = call(session.post, TIKTOK_TOKEN_URL, data={
        "client_key": key, "client_secret": secret, "code": code,
        "grant_type": "authorization_code", "redirect_uri": redirect,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30, context="TikTok token exchange")
    return _save_tiktok(settings, data)


def tiktok_token(settings: Settings, session: requests.Session | None = None, now: datetime | None = None) -> str:
    record = store_for(settings).get("tiktok")
    if not record.get("access_token"):
        raise ConfigError("TikTok is not connected. Run `vauto auth tiktok` first.")
    now = now or _now()
    expires = _parse(record.get("expires_at"))
    if expires and expires - now > timedelta(minutes=5):
        return record["access_token"]
    key, secret, _ = _tiktok_app(settings)
    session = session or requests.Session()
    data = call(session.post, TIKTOK_TOKEN_URL, data={
        "client_key": key, "client_secret": secret, "grant_type": "refresh_token",
        "refresh_token": record.get("refresh_token"),
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30, context="TikTok token refresh")
    return _save_tiktok(settings, data)["access_token"]
