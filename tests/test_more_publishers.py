import json
import shutil
import subprocess
import threading
from datetime import timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from videoautomation import auth
from videoautomation.errors import ConfigError, PublishError
from videoautomation.models import MediaFile, PostJob
from videoautomation.publishers.bluesky import BlueskyPublisher, facets
from videoautomation.publishers.handoff import HandoffPublisher
from videoautomation.publishers.mastodon import MastodonPublisher
from videoautomation.publishers.meta import ThreadsPublisher
from videoautomation.publishers.sau import SauPublisher
from videoautomation.publishers.telegram import TelegramPublisher
from videoautomation.publishers.tiktok import TikTokDirectPublisher, chunk_plan
from videoautomation.publishers.zernio import ZernioPublisher
from videoautomation.scheduler import iso, utcnow

from .conftest import FakeResponse, FakeSession, needs_ffmpeg

FIXTURES = Path(__file__).parent / "fixtures"


def no_sleep(_):
    pass


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00" * 4096)
    return MediaFile(str(p), "video")


@pytest.fixture
def photos(tmp_path):
    out = []
    for i in range(3):
        p = tmp_path / f"p{i}.jpg"
        p.write_bytes(b"jpg" * 10)
        out.append(MediaFile(str(p), "image"))
    return out


class StubStorage:
    name = "stub"

    def put(self, path, key):
        return f"https://cdn.example/{key}"


# ------------------------------------------------------------------- Bluesky


def test_bluesky_facets_use_byte_offsets():
    text = "Café ☀️ #morning https://x.co/a"
    found = facets(text)
    tag = next(f for f in found if f["features"][0]["$type"].endswith("#tag"))
    start = tag["index"]["byteStart"]
    assert text.encode()[start:tag["index"]["byteEnd"]].decode() == "#morning"
    assert tag["features"][0]["tag"] == "morning"
    assert any(f["features"][0]["$type"].endswith("#link") for f in found)


def test_bluesky_video_post(settings, clip, monkeypatch):
    settings.bluesky_handle, settings.bluesky_app_password = "me.bsky.social", "pw"
    monkeypatch.setattr(BlueskyPublisher, "_aspect", staticmethod(lambda m: {"width": 1080, "height": 1920}))
    session = FakeSession({
        ("POST", "createSession"): FakeResponse(data={"accessJwt": "jwt", "did": "did:plc:me", "handle": "me.bsky.social"}),
        ("POST", "uploadBlob"): FakeResponse(data={"blob": {"$type": "blob", "ref": {"$link": "b"}}}),
        ("POST", "createRecord"): FakeResponse(data={"uri": "at://did:plc:me/app.bsky.feed.post/3abc"}),
    })
    result = BlueskyPublisher(settings, session=session).publish(
        PostJob("bluesky", "video", "bluesky", [clip], "Hello #vauto"))
    assert result.url == "https://bsky.app/profile/me.bsky.social/post/3abc"
    record = session.calls[-1][2]["json"]["record"]
    assert record["embed"]["$type"] == "app.bsky.embed.video" and record["facets"]


def test_bluesky_requires_credentials(settings, clip):
    with pytest.raises(ConfigError):
        BlueskyPublisher(settings, session=FakeSession({})).publish(PostJob("bluesky", "video", "bluesky", [clip], "x"))


# ------------------------------------------------------------------ Telegram


def tg_ok(result):
    return FakeResponse(data={"ok": True, "result": result})


def test_telegram_channel_video_and_album(settings, clip, photos):
    settings.telegram_bot_token, settings.telegram_channel_id = "T", "@mychannel"
    session = FakeSession({
        ("POST", "sendVideo"): tg_ok({"message_id": 7, "chat": {"id": -1001, "username": "mychannel"}}),
        ("POST", "sendMediaGroup"): tg_ok([{"message_id": 9, "chat": {"id": -1002345}}]),
    })
    pub = TelegramPublisher(settings, session=session)
    assert pub.publish(PostJob("telegram", "video", "telegram", [clip], "cap")).url == "https://t.me/mychannel/7"
    album = pub.publish(PostJob("telegram", "album", "telegram", photos, "cap"))
    assert album.url == "https://t.me/c/2345/9"
    media = json.loads(session.calls[-1][2]["data"]["media"])
    assert media[0]["caption"] == "cap" and len(media) == 3


def test_telegram_errors_are_classified(settings, clip):
    settings.telegram_bot_token, settings.telegram_channel_id = "T", "@c"
    session = FakeSession({("POST", "sendVideo"): FakeResponse(429, {"ok": False, "error_code": 429,
                                                                      "description": "Too Many Requests"})})
    with pytest.raises(PublishError) as err:
        TelegramPublisher(settings, session=session).publish(PostJob("telegram", "video", "telegram", [clip], "x"))
    assert err.value.transient


# ------------------------------------------------------------------ Mastodon


def test_mastodon_waits_for_video_processing(settings, clip):
    settings.mastodon_instance, settings.mastodon_access_token = "https://mastodon.social", "tok"
    session = FakeSession({
        ("POST", "/api/v2/media"): FakeResponse(202, {"id": "m1", "url": None}),
        ("GET", "/api/v1/media/m1"): [FakeResponse(data={"id": "m1", "url": None}),
                                      FakeResponse(data={"id": "m1", "url": "https://files/m1.mp4"})],
        ("POST", "/api/v1/statuses"): FakeResponse(data={"id": "s1", "url": "https://mastodon.social/@me/s1"}),
    })
    result = MastodonPublisher(settings, session=session, sleep=no_sleep).publish(
        PostJob("mastodon", "video", "mastodon", [clip], "hi"))
    assert result.url.endswith("/s1")
    assert session.calls[-1][2]["data"]["media_ids[]"] == ["m1"]


# -------------------------------------------------------------- TikTok drafts


def test_tiktok_chunk_plan():
    assert chunk_plan(3_000_000) == (3_000_000, 1)
    size, count = chunk_plan(100 * 1024 * 1024)
    assert size == 10 * 1024 * 1024 and count == 10


def test_tiktok_direct_sends_draft(settings, clip):
    auth.store_for(settings).set("tiktok", access_token="at",
                                 expires_at=(utcnow() + timedelta(hours=5)).isoformat())
    session = FakeSession({
        ("POST", "inbox/video/init"): FakeResponse(data={"data": {"publish_id": "pub1", "upload_url": "https://up/1"},
                                                         "error": {"code": "ok"}}),
        ("PUT", "https://up/1"): FakeResponse(201, {}),
        ("POST", "status/fetch"): [FakeResponse(data={"data": {"status": "PROCESSING_UPLOAD"}, "error": {"code": "ok"}}),
                                   FakeResponse(data={"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})],
    })
    result = TikTokDirectPublisher(settings, session=session, sleep=no_sleep).publish(
        PostJob("tiktok", "video", "tiktok", [clip], "x"))
    assert result.status == "draft" and result.remote_id == "pub1"
    init = session.calls[0][2]["json"]["source_info"]
    assert init == {"source": "FILE_UPLOAD", "video_size": 4096, "chunk_size": 4096, "total_chunk_count": 1}
    put = next(c for c in session.calls if c[0] == "PUT")
    assert put[2]["headers"]["Content-Range"] == "bytes 0-4095/4096"


def test_tiktok_direct_rejects_photos(settings, photos):
    with pytest.raises(PublishError, match="video drafts only"):
        TikTokDirectPublisher(settings, session=FakeSession({})).publish(PostJob("tiktok", "photos", "tiktok", photos, "x"))


# ------------------------------------------------------------------- Threads


def test_threads_carousel(settings, photos):
    settings.threads_user_id, settings.threads_access_token = "th1", "ttok"
    auth.store_for(settings).set("threads", access_token="ttok", refreshed_at=utcnow().isoformat())
    ids = iter(["c1", "c2", "c3", "parent"])
    session = FakeSession({
        ("POST", "threads_publish"): FakeResponse(data={"id": "post1"}),
        ("POST", "th1/threads"): lambda url, kw: FakeResponse(data={"id": next(ids)}),
        ("GET", "post1"): FakeResponse(data={"permalink": "https://threads.net/@me/post/1"}),
        ("GET", "graph.threads.net"): FakeResponse(data={"status": "FINISHED"}),
    })
    result = ThreadsPublisher(settings, StubStorage(), session=session, sleep=no_sleep).publish(
        PostJob("threads", "carousel", "threads", photos, "three", post_id="p"))
    assert result.url == "https://threads.net/@me/post/1"
    parent = [c for c in session.calls if c[1].endswith("th1/threads")][-1][2]["data"]
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "c1,c2,c3"


# ---------------------------------------------------------- Zernio new platforms


def test_zernio_maps_x_pinterest_reddit(settings, clip):
    settings.zernio_accounts.update(x="acc_x", pinterest="acc_p", reddit="acc_r")
    settings.pinterest_board_id, settings.reddit_subreddit = "board9", "videos"
    clip.url = "https://cdn/clip.mp4"
    pub = ZernioPublisher(settings, session=FakeSession({}))
    x = pub.build_body(PostJob("x", "video", "zernio", [clip], "hi"))
    assert x["platforms"][0]["platform"] == "twitter"
    pin = pub.build_body(PostJob("pinterest", "video_pin", "zernio", [clip], "desc", options={"title": "T"}))
    assert pin["platforms"][0]["platformSpecificData"] == {"title": "T", "boardId": "board9"}
    red = pub.build_body(PostJob("reddit", "video", "zernio", [clip], "body", options={"title": "Title"}))
    assert red["platforms"][0]["platformSpecificData"] == {"title": "Title", "subreddit": "videos", "nativeVideo": True}


# ------------------------------------------------------------------- hand-off


def test_handoff_saves_outbox_and_sends_to_phone(settings, clip):
    settings.telegram_bot_token, settings.telegram_owner_chat_id = "T", "42"
    session = FakeSession({
        ("POST", "sendMessage"): tg_ok({"message_id": 1}),
        ("POST", "sendDocument"): tg_ok({"message_id": 2}),
    })
    job = PostJob("lemon8", "video", "handoff", [clip], "My caption", options={"title": "My title"}, post_id="p1")
    result = HandoffPublisher(settings, session=session).publish(job)
    folder = settings.outbox_dir / "p1" / "lemon8"
    assert result.status == "handoff"
    assert (folder / "lemon8.mp4").is_file() and (folder / "caption.txt").read_text() == "My caption"
    methods = [c[1].rsplit("/", 1)[-1] for c in session.calls]
    assert methods == ["sendMessage", "sendDocument", "sendMessage", "sendMessage"]


def test_handoff_without_telegram_uses_outbox(settings, clip):
    result = HandoffPublisher(settings, session=FakeSession({})).publish(
        PostJob("kwai", "video", "handoff", [clip], "c", post_id="p2"))
    assert result.status == "handoff" and result.notes[0].startswith("saved to ")
    assert (settings.outbox_dir / "p2" / "kwai" / "kwai.mp4").is_file()


# ------------------------------------------------------ social-auto-upload


def test_sau_command_and_schedule(settings, clip, photos, monkeypatch):
    pub = SauPublisher(settings)
    later = iso(utcnow() + timedelta(hours=3))
    cmd = pub.command(PostJob("douyin", "video", "sau", [clip], "desc", run_at=later,
                              options={"title": "标题", "tags": ["a", "b"]}))
    assert cmd[:3] == ["sau", "douyin", "upload-video"] and "--schedule" in cmd and cmd[cmd.index("--tags") + 1] == "a,b"
    note = pub.command(PostJob("xiaohongshu", "note", "sau", photos, "note text", options={"title": "t"}))
    assert note[2] == "upload-note" and note.count("--images") == 1
    bili = pub.command(PostJob("bilibili", "video", "sau", [clip], "d", options={"title": "t"}))
    assert "--tid" in bili
    weibo = pub.command(PostJob("weibo", "video", "sau", [clip], "d", run_at=later, options={"title": "t"}))
    assert "--schedule" not in weibo  # weibo cannot schedule

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/sau")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "cookie expired, 请重新登录"))
    with pytest.raises(PublishError, match="login --account"):
        pub.publish(PostJob("douyin", "video", "sau", [clip], "d", options={"title": "t"}))


def test_sau_missing_install(settings, clip, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="social-auto-upload"):
        SauPublisher(settings).publish(PostJob("douyin", "video", "sau", [clip], "d"))


# ---------------------------------------------------------- browser automation


def _chromium():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    for path in ("/opt/pw-browsers/chromium",):
        if Path(path).exists():
            return path
    return "default"


@pytest.fixture
def fake_site():
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.skipif(_chromium() is None, reason="playwright not installed")
def test_browser_flow_uploads_on_fake_site(settings, clip, fake_site, tmp_path):
    from videoautomation.publishers.browser import BrowserPublisher, _load

    flows = tmp_path / "flows.yaml"
    flows.write_text(f"""flows:
  rutube:
    login_url: {fake_site}/fake_upload.html
    upload_url: {fake_site}/fake_upload.html
    steps:
      - {{action: goto, url: "{{upload_url}}"}}
      - {{action: upload}}
      - {{action: fill, selectors: ["#title"], value: "{{title}}"}}
      - {{action: fill, selectors: ["#desc"], value: "{{description}}"}}
      - {{action: click_text, text: ["Publish"]}}
      - {{action: wait_text, text: ["Published!"], timeout_s: 10}}
""")
    _load.cache_clear()
    settings.env["VAUTO_BROWSER_FLOWS"] = str(flows)
    if _chromium() != "default":
        settings.browser_path = _chromium()
    job = PostJob("rutube", "video", "browser", [clip], "Line one\nmore", options={"title": "My title"}, post_id="b1")
    result = BrowserPublisher(settings).publish(job)
    assert result.status == "published"
    assert "submitted=My%20title" in result.url or "submitted=My title" in result.url


@pytest.mark.skipif(_chromium() is None, reason="playwright not installed")
def test_browser_failure_falls_back_to_handoff(settings, clip, fake_site, tmp_path):
    from videoautomation.publishers.browser import BrowserPublisher, _load

    flows = tmp_path / "flows.yaml"
    flows.write_text(f"""flows:
  rutube:
    login_url: {fake_site}/fake_upload.html
    upload_url: {fake_site}/fake_upload.html
    steps:
      - {{action: goto, url: "{{upload_url}}"}}
      - {{action: click_text, text: ["No such button"], timeout_s: 1}}
""")
    _load.cache_clear()
    settings.env["VAUTO_BROWSER_FLOWS"] = str(flows)
    if _chromium() != "default":
        settings.browser_path = _chromium()
    result = BrowserPublisher(settings).publish(PostJob("rutube", "video", "browser", [clip], "c", post_id="b2"))
    assert result.status == "handoff"
    assert "browser automation failed" in result.notes[0]
    assert list((settings.home / "browser" / "debug").glob("rutube-*.png"))
