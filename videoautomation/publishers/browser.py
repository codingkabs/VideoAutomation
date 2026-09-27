"""Browser automation for platforms that only have a web uploader.

Runs the steps from config/browser_flows.yaml in a persistent Chromium profile
per platform (saved by `vauto browser login <platform>`). Needs Playwright:
`pip install 'vauto[browser]'` then `playwright install chromium`, or point
VAUTO_BROWSER_PATH at an existing Chrome/Chromium.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings
from ..errors import ConfigError, PublishError
from ..models import PostJob, PostResult
from .base import Publisher
from .handoff import HandoffPublisher

FLOWS_FILE = Path(__file__).resolve().parent.parent / "config" / "browser_flows.yaml"
KNOWN_CHROMIUM = ("/opt/pw-browsers/chromium",)


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)["flows"]


def load_flows(settings: Settings) -> dict[str, Any]:
    custom = settings.env.get("VAUTO_BROWSER_FLOWS")
    flows = dict(_load(str(FLOWS_FILE)))
    if custom:
        flows.update(_load(str(Path(custom).expanduser())))
    return flows


def flow_for(settings: Settings, platform: str) -> dict[str, Any]:
    flows = load_flows(settings)
    if platform not in flows:
        raise ConfigError(f"No browser flow for {platform}")
    return flows[platform]


def browser_executable(settings: Settings) -> str | None:
    if settings.browser_path:
        return settings.browser_path
    for candidate in KNOWN_CHROMIUM:
        if os.path.exists(candidate):
            return candidate
    return None


class BrowserSession:
    """Persistent Chromium profile for one platform."""

    def __init__(self, settings: Settings, platform: str, headless: bool | None = None):
        self.settings = settings
        self.platform = platform
        self.headless = settings.browser_headless if headless is None else headless
        self.profile = settings.home / "browser" / platform
        self._pw = None
        self.context = None

    def __enter__(self) -> "BrowserSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ConfigError("Browser automation needs Playwright: pip install 'vauto[browser]'") from exc
        self.profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs: dict[str, Any] = {"headless": self.headless, "viewport": {"width": 1366, "height": 900},
                                  "locale": "en-GB"}
        exe = browser_executable(self.settings)
        if exe:
            kwargs["executable_path"] = exe
        try:
            self.context = self._pw.chromium.launch_persistent_context(str(self.profile), **kwargs)
        except Exception as exc:
            self._pw.stop()
            raise ConfigError(f"Could not start Chromium ({exc}). Run `playwright install chromium` "
                              "or set VAUTO_BROWSER_PATH.") from exc
        return self

    def __exit__(self, *exc: Any) -> None:
        if self.context is not None:
            self.context.close()
        if self._pw is not None:
            self._pw.stop()

    def page(self):
        pages = self.context.pages
        return pages[0] if pages else self.context.new_page()


# -------------------------------------------------------------------- steps


def _fill_template(value: str, values: dict[str, str]) -> str:
    for key, text in values.items():
        value = value.replace("{" + key + "}", text)
    return value


def _visible_first(page, selectors: list[str], timeout_ms: int):
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    while True:
        for selector in selectors:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 5)):
                item = loc.nth(i)
                if item.is_visible():
                    return item
        if datetime.now().timestamp() > deadline:
            return None
        page.wait_for_timeout(300)


def _by_text(page, texts: list[str], timeout_ms: int):
    pattern = re.compile("|".join(re.escape(t) for t in texts), re.IGNORECASE)
    deadline = datetime.now().timestamp() + timeout_ms / 1000
    while True:
        for loc in (page.get_by_role("button", name=pattern), page.get_by_text(pattern)):
            for i in range(min(loc.count(), 5)):
                item = loc.nth(i)
                if item.is_visible() and item.is_enabled():
                    return item
        if datetime.now().timestamp() > deadline:
            return None
        page.wait_for_timeout(300)


def run_steps(page, flow: dict[str, Any], files: list[str], values: dict[str, str]) -> None:
    values = {**values, "upload_url": flow.get("upload_url", ""), "login_url": flow.get("login_url", "")}
    for number, step in enumerate(flow["steps"], 1):
        action = step["action"]
        optional = step.get("optional", False)
        timeout_ms = int(step.get("timeout_s", 30) * 1000)
        what = f"step {number} ({action})"
        if action == "goto":
            page.goto(_fill_template(step["url"], values), wait_until="domcontentloaded")
            if "login" in page.url.lower() and "login" not in flow.get("upload_url", "").lower():
                raise PublishError("not logged in; run `vauto browser login <platform>`")
        elif action == "upload":
            selector = step.get("selector", "input[type=file]")
            loc = page.locator(selector).first
            try:
                loc.wait_for(state="attached", timeout=timeout_ms)
                loc.set_input_files(files)
            except Exception as exc:
                if not optional:
                    raise PublishError(f"{what}: no file input found ({selector})") from exc
        elif action == "fill":
            item = _visible_first(page, step["selectors"], timeout_ms)
            if item is None:
                if optional:
                    continue
                raise PublishError(f"{what}: none of {step['selectors']} is visible")
            item.click()
            item.fill(_fill_template(step["value"], values))
        elif action == "click":
            item = _visible_first(page, [step["selector"]], timeout_ms)
            if item is None:
                if optional:
                    continue
                raise PublishError(f"{what}: {step['selector']} not found")
            item.click()
        elif action == "click_text":
            item = _by_text(page, step["text"], timeout_ms)
            if item is None:
                if optional:
                    continue
                raise PublishError(f"{what}: no button labelled {step['text']}")
            item.click()
        elif action == "wait":
            page.wait_for_timeout(int(step.get("seconds", 1) * 1000))
        elif action == "wait_text":
            if _by_text(page, step["text"], timeout_ms) is None and not optional:
                raise PublishError(f"{what}: did not see {step['text']} within {timeout_ms // 1000}s")
        elif action == "wait_url":
            try:
                page.wait_for_url(re.compile(re.escape(step["contains"])), timeout=timeout_ms)
            except Exception as exc:
                if not optional:
                    raise PublishError(f"{what}: URL never contained {step['contains']!r}") from exc
        else:
            raise ConfigError(f"Unknown browser step action {action!r}")


class BrowserPublisher(Publisher):
    name = "browser"

    def debug_dir(self) -> Path:
        path = self.settings.home / "browser" / "debug"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish(self, job: PostJob) -> PostResult:
        flow = flow_for(self.settings, job.platform)
        values = {
            "title": job.options.get("title") or job.caption.split("\n", 1)[0][:100],
            "description": job.caption,
            "caption": job.caption,
            "tags": " ".join("#" + t for t in job.options.get("tags") or []),
        }
        files = [m.path for m in job.media]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            with BrowserSession(self.settings, job.platform) as browser:
                page = browser.page()
                try:
                    run_steps(page, flow, files, values)
                    url = page.url
                except Exception as exc:
                    shot = self.debug_dir() / f"{job.platform}-{stamp}.png"
                    try:
                        page.screenshot(path=str(shot), full_page=True)
                        (shot.with_suffix(".html")).write_text(page.content(), encoding="utf-8")
                    except Exception:
                        pass
                    raise PublishError(f"{job.platform} browser upload failed: {exc} (screenshot: {shot})") from exc
        except (PublishError, ConfigError) as exc:
            if self.settings.browser_fallback_handoff:
                result = HandoffPublisher(self.settings, self.storage, self.session).publish(job)
                result.notes.insert(0, f"browser automation failed, handed off instead: {exc}")
                return result
            raise
        return PostResult(job.platform, job.surface, "published", url=url,
                          notes=["posted through browser automation; check the post looks right"])


def login(settings: Settings, platform: str) -> str:
    """Open a visible browser on the platform's login page and keep the session."""
    flow = flow_for(settings, platform)
    with BrowserSession(settings, platform, headless=False) as browser:
        page = browser.page()
        page.goto(flow["login_url"])
        input(f"Log in to {platform} in the browser window, then press Enter here to save the session... ")
    return str(browser.profile)


def check(settings: Settings, platform: str) -> bool:
    """True when the saved session reaches the upload page and it has a file input."""
    flow = flow_for(settings, platform)
    with BrowserSession(settings, platform, headless=True) as browser:
        page = browser.page()
        page.goto(flow["upload_url"], wait_until="domcontentloaded")
        try:
            page.locator("input[type=file]").first.wait_for(state="attached", timeout=20000)
            return True
        except Exception:
            return False
