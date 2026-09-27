import json
import shutil
import subprocess
from pathlib import Path

import pytest

from videoautomation.config import Settings

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("media")
    # 6 s landscape clip with audio
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi",
            "-i", "sine=frequency=440:sample_rate=48000", "-t", "6", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(d / "landscape.mp4"))
    # 4 s vertical clip without audio
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=540x960:rate=25", "-t", "4", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", str(d / "vertical_silent.mp4"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=800x1000", "-frames:v", "1", str(d / "a.png"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1600x900", "-frames:v", "1", str(d / "b.jpg"))
    return d


@pytest.fixture
def settings(tmp_path) -> Settings:
    env = {
        "VAUTO_HOME": str(tmp_path / "home"),
        "IG_USER_ID": "ig123",
        "IG_ACCESS_TOKEN": "igtoken",
        "FB_PAGE_ID": "page1",
        "FB_PAGE_ACCESS_TOKEN": "fbtoken",
        "ZERNIO_API_KEY": "sk_test",
        "ZERNIO_ACCOUNT_TIKTOK": "acc_tt",
        "ZERNIO_ACCOUNT_YOUTUBE": "acc_yt",
        "ZERNIO_ACCOUNT_SNAPCHAT": "acc_sc",
        "ZERNIO_ACCOUNT_INSTAGRAM": "acc_ig",
        "VAUTO_STORAGE": "none",
    }
    return Settings.from_env(env=env, dotenv=None)


class FakeResponse:
    def __init__(self, status: int = 200, data=None):
        self.status_code = status
        self._data = data if data is not None else {}
        self.text = json.dumps(self._data)

    def json(self):
        return self._data


class FakeSession:
    """Routes requests by (method, url substring) to canned responses.

    A route value can be a FakeResponse, a list (returned in order, last one
    repeats), or a callable taking (url, kwargs)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def _handle(self, method, url, kwargs):
        body = kwargs.get("data")
        if hasattr(body, "read"):
            kwargs = {**kwargs, "data": f"<file {len(body.read())} bytes>"}
        self.calls.append((method, url, kwargs))
        for (m, fragment), value in self.routes.items():
            if m == method and fragment in url:
                if callable(value) and not isinstance(value, FakeResponse):
                    return value(url, kwargs)
                if isinstance(value, list):
                    return value.pop(0) if len(value) > 1 else value[0]
                return value
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kwargs):
        return self._handle("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, kwargs)

    def put(self, url, **kwargs):
        return self._handle("PUT", url, kwargs)
