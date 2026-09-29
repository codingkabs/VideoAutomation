"""Fixes from the full code review: phone media quirks, setup routing, honest messages, disk clean-up."""

import os
import struct
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest

from videoautomation import cli
from videoautomation.config import Settings
from videoautomation.ffmpeg import has_filter, probe
from videoautomation.media.normalize import normalize_video
from videoautomation.media.variants import VariantOptions, variant_graph
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import iso, utcnow
from videoautomation.tracker import Tracker, cleanup, housekeeping

from .conftest import HAS_FFMPEG, needs_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _has_encoder(name: str) -> bool:
    if not HAS_FFMPEG:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    return f" {name} " in out


# ------------------------------------------------------------------- media


@pytest.mark.skipif(not (_has_encoder("libx265") and HAS_FFMPEG and has_filter("zscale")),
                    reason="needs ffmpeg with libx265 and zscale")
def test_iphone_hdr_video_is_tone_mapped_to_standard_colour(tmp_path):
    src = tmp_path / "hdr.mov"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30", "-t", "1", "-c:v", "libx265",
            "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020",
            "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", str(src))
    info = probe(src)
    assert info.is_hdr and info.color_transfer == "arib-std-b67"
    _, out = normalize_video(info, tmp_path / "master.mp4")
    assert out.vcodec == "h264" and not out.is_hdr and out.color_transfer == "bt709"


def _jpeg_with_exif_orientation(src: Path, dest: Path, orientation: int) -> None:
    """Insert a minimal EXIF block (Orientation tag only) after the JPEG start marker."""
    tiff = b"II*\x00" + struct.pack("<I", 8) + struct.pack("<H", 1)
    tiff += struct.pack("<HHII", 0x0112, 3, 1, orientation) + struct.pack("<I", 0)
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload
    data = src.read_bytes()
    dest.write_bytes(data[:2] + app1 + data[2:])


@needs_ffmpeg
def test_sideways_phone_photo_reports_its_upright_size(tmp_path):
    raw = tmp_path / "raw.jpg"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1600x1200", "-frames:v", "1", str(raw))
    photo = tmp_path / "portrait.jpg"
    _jpeg_with_exif_orientation(raw, photo, 6)  # "rotate 90° to view", as phones store portrait shots
    info = probe(photo)
    assert (info.width, info.height) == (1200, 1600)
    assert (probe(raw).width, probe(raw).height) == (1600, 1200)


def test_hook_text_is_drawn_literally(tmp_path):
    graph, _ = variant_graph(probe_stub(), VariantOptions(hook_text="100% real %{pts}"), tmp_path)
    assert "expansion=none" in graph


def probe_stub():
    from videoautomation.models import MediaInfo

    return MediaInfo(path="x.mp4", width=1080, height=1920, duration=5, fps=30, vcodec="h264", acodec="aac",
                     size_bytes=1, is_image=False)


# ----------------------------------------------------------------- routing


def test_zernio_is_used_when_the_direct_route_is_not_set_up():
    base = {"VAUTO_HOME": "/tmp/x", "ZERNIO_API_KEY": "k", "ZERNIO_ACCOUNT_INSTAGRAM": "a",
            "ZERNIO_ACCOUNT_FACEBOOK": "b", "ZERNIO_ACCOUNT_BLUESKY": "c"}
    s = Settings.from_env(env=base, dotenv=None)
    assert (s.backends["instagram"], s.backends["facebook"], s.backends["bluesky"]) == ("zernio",) * 3
    direct = Settings.from_env(env={**base, "IG_USER_ID": "1", "IG_ACCESS_TOKEN": "t"}, dotenv=None)
    assert direct.backends["instagram"] == "meta" and direct.backends["facebook"] == "zernio"
    chosen = Settings.from_env(env={**base, "VAUTO_BACKEND_INSTAGRAM": "meta"}, dotenv=None)
    assert chosen.backends["instagram"] == "meta"  # an explicit choice always wins
    nothing = Settings.from_env(env={"VAUTO_HOME": "/tmp/x"}, dotenv=None)
    assert nothing.backends["instagram"] == "meta"  # no Zernio account: keep the documented default


# ----------------------------------------------------------------- messages


def _run(monkeypatch, capsys, settings, *argv):
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


@pytest.mark.parametrize("exchanged", [True, False])
def test_meta_setup_is_honest_about_token_expiry(monkeypatch, capsys, settings, tmp_path, exchanged):
    from videoautomation import auth

    monkeypatch.setattr(auth, "meta_setup", lambda s, token: {
        "long_lived_user_token": "L", "exchanged": exchanged,
        "pages": [{"page_id": "1", "page_name": "Me", "page_token": "P", "ig_user_id": "9", "ig_username": "me"}]})
    settings.dotenv_path = tmp_path / ".env"
    code, out = _run(monkeypatch, capsys, settings, "auth", "meta", "--user-token", "x", "--write")
    assert code == 0 and "Saved" in out
    if exchanged:
        assert "do not expire" in out and "expire in about an hour" not in out
    else:
        assert "expire in about an hour" in out and "do not expire" not in out
    assert "IG_ACCESS_TOKEN=P" in settings.dotenv_path.read_text()


# ------------------------------------------------------------------ clean-up


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _file(path: Path, size: int = 1000, days: float = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    _age(path, days)
    return path


def test_cleanup_frees_old_files_but_keeps_thumbnails_and_queued_posts(settings):
    tr = Tracker.open(settings)
    old_post = settings.renders_dir / "oldpost"
    master = _file(old_post / "master_auto.mp4", 5000, days=30)
    thumb = _file(old_post / "thumb.jpg", 100, days=30)
    subs = _file(old_post / "subtitles.srt", 50, days=30)
    fresh = _file(settings.renders_dir / "newpost" / "master_auto.mp4", 5000, days=1)
    queued = _file(settings.renders_dir / "queuedpost" / "master_auto.mp4", 5000, days=30)
    job = PostJob("instagram", "reel", "meta", [MediaFile(str(queued), "video")], "c", idem_key="q1",
                  post_id="queuedpost", run_at=iso(utcnow() + timedelta(days=1)))
    tr.store.insert(job, "pending")
    upload = _file(settings.home / "uploads" / "abc123" / "00_clip.mp4", 3000, days=20)
    new_upload = _file(settings.home / "uploads" / "def456" / "00_clip.mp4", 3000, days=2)
    handoff = _file(settings.outbox_dir / "oldpost" / "tiktok" / "tiktok.mp4", 2000, days=20)
    bot_file = _file(settings.home / "inbox" / "55" / "20260901-100000" / "media_00.mp4", 1000, days=20)

    preview = cleanup(settings, tr, days=14, dry_run=True)
    assert preview == {"files": 4, "bytes": 11000} and master.exists()

    freed = cleanup(settings, tr, days=14)
    assert freed == preview
    assert not master.exists() and thumb.exists() and subs.exists()
    assert fresh.exists() and queued.exists() and new_upload.exists()
    assert not upload.exists() and not upload.parent.exists()
    assert not handoff.exists() and not (settings.outbox_dir / "oldpost").exists()
    assert not bot_file.exists() and (settings.home / "inbox" / "55").exists()
    assert cleanup(settings, tr, days=0) == {"files": 0, "bytes": 0}


def test_worker_cleans_up_once_a_day(settings):
    tr = Tracker.open(settings)
    settings.stats_refresh_hours = 0
    _file(settings.renders_dir / "p" / "master_auto.mp4", 2_000_000, days=30)
    now = utcnow()
    assert housekeeping(settings, tr, now=now) == ["cleaned up 1 old file(s), 2 MB"]
    _file(settings.renders_dir / "p2" / "master_auto.mp4", 1000, days=30)
    assert housekeeping(settings, tr, now=now + timedelta(hours=2)) == []  # already ran today
    assert housekeeping(settings, tr, now=now + timedelta(days=1, minutes=1))[0].startswith("cleaned up 1")


def test_clean_command(monkeypatch, capsys, settings):
    _file(settings.renders_dir / "p" / "master_auto.mp4", 3_000_000, days=30)
    code, out = _run(monkeypatch, capsys, settings, "clean", "--dry-run")
    assert code == 0 and "Would delete 1 file(s) older than 14 days, 3 MB" in out
    code, out = _run(monkeypatch, capsys, settings, "clean", "--days", "7")
    assert "Deleted 1 file(s)" in out and not (settings.renders_dir / "p" / "master_auto.mp4").exists()


def test_keep_files_setting():
    assert Settings.from_env(env={"VAUTO_HOME": "/tmp/x"}, dotenv=None).keep_files_days == 14
    assert Settings.from_env(env={"VAUTO_HOME": "/tmp/x", "VAUTO_KEEP_FILES_DAYS": "0"}, dotenv=None).keep_files_days == 0
    from videoautomation.errors import ConfigError

    with pytest.raises(ConfigError, match="VAUTO_KEEP_FILES_DAYS"):
        Settings.from_env(env={"VAUTO_HOME": "/tmp/x", "VAUTO_KEEP_FILES_DAYS": "two weeks"}, dotenv=None)


@needs_ffmpeg
def test_plan_mentions_hdr_conversion(settings, tmp_path):
    if not (_has_encoder("libx265") and has_filter("zscale")):
        pytest.skip("needs libx265 and zscale")
    from videoautomation import service

    src = tmp_path / "hdr.mov"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30", "-t", "3", "-c:v", "libx265",
            "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc", "-pix_fmt", "yuv420p10le", "-color_trc", "arib-std-b67",
            "-color_primaries", "bt2020", "-colorspace", "bt2020nc", str(src))
    req = service.make_request(settings, [src], "hdr test", ["instagram"], dry_run=True)
    plan, _ = service.post(settings, req)
    assert any("HDR video converted" in n for n in plan.notes)


def test_results_do_not_change_for_standard_video(settings):
    """Non-HDR sources skip tone-mapping entirely (no behaviour change for most phone exports)."""
    assert not probe_stub().is_hdr
    assert PostResult("x", "y", "published").ok


def test_phone_address_behind_a_proxy_is_not_treated_as_cross_site(settings, tmp_path):
    pytest.importorskip("flask")
    import base64

    from videoautomation.web.app import create_app

    settings.web_password = "pw"
    settings.dotenv_path = tmp_path / ".env"
    settings.env["VAUTO_PUBLIC_URL"] = "https://my-pc.tail1234.ts.net"
    client = create_app(settings).test_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"vauto:pw").decode()}
    phone = {**auth, "Origin": "https://my-pc.tail1234.ts.net"}
    # tailscale serve may forward to 127.0.0.1:8765 with the original name in X-Forwarded-Host
    ok = client.post("/api/snippets", json={"name": "a", "text": "b"}, headers=phone,
                     base_url="http://127.0.0.1:8765")
    assert ok.status_code == 200
    proxied = client.post("/api/snippets", json={"name": "c", "text": "d"},
                          headers={**auth, "Origin": "https://other.ts.net", "X-Forwarded-Host": "other.ts.net"},
                          base_url="http://127.0.0.1:8765")
    assert proxied.status_code == 200
    evil = client.post("/api/snippets", json={"name": "e", "text": "f"},
                       headers={**auth, "Origin": "https://evil.example"}, base_url="http://127.0.0.1:8765")
    assert evil.status_code == 403


def test_env_example_copy_uses_zernio_for_instagram_and_facebook(tmp_path):
    """A fresh `copy .env.example .env` plus Zernio accounts must route Instagram and Facebook to Zernio."""
    from pathlib import Path

    env = tmp_path / ".env"
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    env.write_text(example + "\nZERNIO_API_KEY=k\nZERNIO_ACCOUNT_INSTAGRAM=a\nZERNIO_ACCOUNT_FACEBOOK=b\n",
                   encoding="utf-8")
    s = Settings.from_env(env={"VAUTO_HOME": str(tmp_path / "home")}, dotenv=env)
    assert s.backends["instagram"] == "zernio" and s.backends["facebook"] == "zernio"
