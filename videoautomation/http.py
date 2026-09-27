"""HTTP helpers shared by publishers, auth and notifications."""

from __future__ import annotations

from typing import Any, Callable

import requests

from .errors import PublishError


def transient_status(status: int) -> bool:
    return status == 429 or status >= 500


def error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300] or f"HTTP {resp.status_code}"
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            msg = err.get("error_user_msg") or err.get("message") or str(err)
            code = err.get("code")
            return f"{msg} (code {code})" if code else msg
        if isinstance(err, str):
            return err
        if "message" in data:
            return str(data["message"])
    return str(data)[:300]


def check(resp: requests.Response, context: str) -> dict[str, Any]:
    """Return JSON for a 2xx response, otherwise raise PublishError."""
    if resp.status_code >= 400:
        transient = transient_status(resp.status_code)
        try:
            err = resp.json().get("error", {})
            if isinstance(err, dict) and err.get("is_transient"):
                transient = True
        except (ValueError, AttributeError):
            pass
        raise PublishError(f"{context}: {error_message(resp)}", transient=transient)
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


def call(fn: Callable[..., requests.Response], *args: Any, context: str, **kwargs: Any) -> dict[str, Any]:
    """Run a requests call, turning network failures into transient PublishErrors."""
    try:
        resp = fn(*args, **kwargs)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise PublishError(f"{context}: network error ({exc})", transient=True) from exc
    return check(resp, context)
