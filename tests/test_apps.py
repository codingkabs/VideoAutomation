"""Trial results, Telegram bot, web app, doctor and CLI."""

import io
import json
from datetime import timedelta
from pathlib import Path

import pytest

from videoautomation import cli, insights, service
from videoautomation.bot import Bot
from videoautomation.config import Settings
from videoautomation.doctor import platform_ready, run_checks
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import JobStore, iso, utcnow

from .conftest import FakeResponse, FakeSession, needs_ffmpeg

# ---------------------------------------------------------------- insights


def _store_trial(settings, main_result, trial_result, backend="meta"):
    store = JobStore(settings.db_path)
    main = PostJob("instagram", "reel", backend, [], "c", idem_key="main1", post_id="p1")
    trial = PostJob("instagram", "trial_reel", backend, [], "c", idem_key="trial1", post_id="p1", depends_on="main1")
    for job, result in ((main, main_result), (trial, trial_result)):
        store.insert(job, "running")
        store.finish(job.idem_key, result)
    return store


def test_trial_comparison_picks_winner(settings):
    store = _store_trial(settings,
                         PostResult("instagram", "reel", "published", url="u1", platform_post_id="m1"),
                         PostResult("instagram", "trial_reel", "published", url="u2", platform_post_id="m2"))
    metrics = {"m1": {"views": 1000, "likes": 50}, "m2": {"views": 3000, "likes": 90, "shares": 20}}

    def fake_get(url, params=None, **kw):
        media = url.split("/")[-2]
        return FakeResponse(data={"data": [{"name": k, "values": [{"value": v}]} for k, v in metrics[media].items()]})

    session = FakeSession({("GET", "/insights"): lambda url, kw: fake_get(url, **kw)})
    comparison = insights.compare(settings, store, "trial1", session)
    assert comparison.winner == "trial"
    assert comparison.main["views"] == 1000 and comparison.trial["shares"] == 20
    assert any("share it with followers" in line for line in comparison.lines())


def test_trial_comparison_reports_missing_side(settings):
    store = _store_trial(settings, PostResult("instagram", "reel", "failed", error="x"),
                         PostResult("instagram", "trial_reel", "skipped"))
    comparison = insights.compare(settings, store, "trial1", FakeSession({}))
    assert comparison.winner is None and "main reel has not published" in comparison.notes


def test_insights_job_notifies(settings, monkeypatch):
    store = _store_trial(settings,
                         PostResult("instagram", "reel", "published", platform_post_id="m1"),
                         PostResult("instagram", "trial_reel", "published", platform_post_id="m2"))
    sent = []
    monkeypatch.setattr(insights, "metrics_for", lambda s, b, r, sess: {"views": 10 if r.platform_post_id == "m1" else 5})
    monkeypatch.setattr(insights.notify, "send", lambda s, text, session=None: sent.append(text) or True)
    job = PostJob("instagram", "trial_report", "insights", [], "", options={"trial_key": "trial1"})
    result = insights.InsightsPublisher(settings, session=FakeSession({})).publish(job)
    assert result.status == "reported" and "winner: main reel" in result.notes
    assert sent and "Trial Reel results" in sent[0]


# --------------------------------------------------------------------- bot


class FakeTelegram:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []
        self.next_id = 100

    def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.next_id += 1
        self.sent.append({"chat": chat_id, "text": text, "markup": reply_markup})
        return {"message_id": self.next_id}

    def edit_message(self, chat_id, message_id, text, reply_markup=None, parse_mode=None):
        self.edits.append({"chat": chat_id, "id": message_id, "text": text, "markup": reply_markup})

    def answer_callback(self, callback_id, text=None):
        self.answers.append(text)

    def download(self, file_id, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"x")
        return dest


class InlineExecutor:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def bot(settings, monkeypatch):
    settings.telegram_bot_token = "T"
    settings.telegram_allowed_user_ids = [7]
    api = FakeTelegram()
    b = Bot(settings, api=api, executor=InlineExecutor(), api_factory=lambda: api)
    calls = []

    def fake_post(s, req):
        calls.append(req)
        plan = type("P", (), {"post_id": "abc", "notes": ["n1"]})()
        status = "dry_run" if req.dry_run else "published"
        return plan, [PostResult("instagram", "reel", status, url=None if req.dry_run else "https://ig/p")]

    monkeypatch.setattr(service, "post", fake_post)
    b.calls = calls
    return b


def msg(text=None, user=7, **extra):
    return {"update_id": 1, "message": {"chat": {"id": 55}, "from": {"id": user}, "text": text, **extra}}


def test_bot_is_private(bot):
    bot.handle_update(msg("hi", user=999))
    assert "Your Telegram user id is 999" in bot.api.sent[-1]["text"]
    assert 55 not in bot.sessions


def test_bot_video_caption_preview_and_post(bot):
    bot.handle_update(msg(video={"file_id": "F", "file_size": 1000}, caption="Hello #uk"))
    panel = bot.api.sent[-1]
    assert "Ready to post" in panel["text"] and panel["markup"]["inline_keyboard"]
    session = bot.sessions[55]
    assert session.caption == "Hello #uk" and session.files[0].name == "media_00.mp4"

    def press(data):
        bot.handle_update({"update_id": 2, "callback_query": {"id": "q", "from": {"id": 7}, "data": data,
                                                              "message": {"chat": {"id": 55}, "message_id": session.panel_id}}})

    press("trial")
    assert session.trial is True
    press("draft")
    press("preview")
    assert bot.calls[-1].dry_run and bot.calls[-1].trial and bot.calls[-1].tiktok_draft
    assert "Preview" in bot.api.edits[-1]["text"]
    press("post")
    assert not bot.calls[-1].dry_run
    assert "https://ig/p" in bot.api.edits[-1]["text"]
    assert 55 not in bot.sessions


def test_bot_asks_for_caption_and_groups_photos(bot):
    for i in range(3):
        bot.handle_update(msg(photo=[{"file_id": f"s{i}"}, {"file_id": f"L{i}", "file_size": 100}], media_group_id="g1"))
    assert 55 not in bot.sessions
    bot.flush_groups(force=True)
    assert len(bot.sessions[55].files) == 3
    assert "send the caption" in bot.api.sent[-1]["text"]
    bot.handle_update(msg("my caption"))
    assert bot.sessions[55].caption == "my caption"
    assert "Ready to post" in bot.api.sent[-1]["text"]


def test_bot_rejects_big_files(bot):
    bot.poll_once = None  # not used
    with pytest.raises(Exception, match="20 MB"):
        bot.handle_update(msg(video={"file_id": "F", "file_size": 60 * 1024 * 1024}, caption="x"))


def test_bot_status_command(bot):
    bot.handle_update(msg("/status"))
    assert "Instagram" in bot.api.sent[-1]["text"]


# ----------------------------------------------------------------- doctor


def test_platform_ready_matrix(settings):
    assert platform_ready(settings, "instagram")[0]
    assert platform_ready(settings, "tiktok")[0]
    assert not platform_ready(settings, "x")[0]
    assert "ZERNIO_ACCOUNT_X" in platform_ready(settings, "x")[1]
    assert platform_ready(settings, "lemon8") == (True, "hand-off to the outbox folder (add Telegram to get it on your phone)")
    settings.backends["tiktok"] = "tiktok"
    assert "vauto auth tiktok" in platform_ready(settings, "tiktok")[1]


def test_run_checks_flags_overdue_queue(settings):
    store = JobStore(settings.db_path)
    store.insert(PostJob("instagram", "trial_reel", "meta", [], "c", idem_key="late",
                         run_at=iso(utcnow() - timedelta(hours=1))))
    checks = run_checks(settings)
    queue = next(c for c in checks if c.name == "local queue")
    assert queue.status == "warn" and "1 overdue" in queue.detail
    assert any(c.area == "platforms" for c in checks)


# -------------------------------------------------------------------- web


@pytest.fixture
def client(settings, tmp_path):
    flask = pytest.importorskip("flask")  # noqa: F841
    from videoautomation.web.app import create_app

    settings.dotenv_path = tmp_path / ".env"
    app = create_app(settings)
    app.config["TESTING"] = True
    return app.test_client()


def test_web_status_and_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    data = client.get("/api/status").get_json()
    assert len(data["platforms"]) >= 50 and data["defaults"]["timezone"] == "Europe/London"
    ig = next(p for p in data["platforms"] if p["key"] == "instagram")
    assert ig["ready"] and ig["caption"]["max_hashtags"] == 5


@needs_ffmpeg
def test_web_upload_preview_flow(client, media_dir):
    with open(media_dir / "landscape.mp4", "rb") as fh:
        up = client.post("/api/upload", data={"files": (fh, "clip.mp4")}, content_type="multipart/form-data")
    body = up.get_json()
    assert up.status_code == 200 and body["files"][0]["kind"] == "video" and body["files"][0]["poster"]
    task = client.post("/api/tasks", json={"action": "preview", "upload_id": body["upload_id"],
                                           "caption": "Hi #a #b #c #d #e #f", "platforms": ["instagram", "tiktok"],
                                           "options": {"trial": True}}).get_json()
    import time
    for _ in range(300):
        state = client.get(f"/api/tasks/{task['task_id']}").get_json()
        if state["state"] != "running":
            break
        time.sleep(0.5)
    assert state["state"] == "done", state
    result = state["result"]
    labels = [r["label"] for r in result["results"]]
    assert labels[:2] == ["instagram", "tiktok"] and "instagram trial reel" in labels
    ig = next(j for j in result["jobs"] if j["platform"] == "instagram" and j["surface"] == "reel")
    assert "#f" not in ig["caption"] and ig["media"][0]["url"].startswith("/media/")
    assert client.get(ig["media"][0]["url"]).status_code == 200


def test_web_settings_round_trip(client, settings):
    data = client.get("/api/settings").get_json()
    zernio = next(g for g in data["groups"] if g["id"] == "zernio")
    key = next(f for f in zernio["fields"] if f["key"] == "ZERNIO_API_KEY")
    assert key["secret"] and key["value"].startswith("•")
    resp = client.post("/api/settings", json={"values": {"ZERNIO_API_KEY": key["value"], "REDDIT_SUBREDDIT": "videos",
                                                         "NOT_A_SETTING": "x"}})
    assert resp.get_json()["changed"] == ["REDDIT_SUBREDDIT"]
    text = settings.dotenv_path.read_text()
    assert "REDDIT_SUBREDDIT=videos" in text and "ZERNIO_API_KEY" not in text and "NOT_A_SETTING" not in text


def test_web_password_and_path_safety(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.web_password = "s3cret"
    client = create_app(settings).test_client()
    assert client.get("/api/status").status_code == 401
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    assert client.get("/api/status", headers=ok).status_code == 200
    assert client.get("/media/../../etc/passwd", headers=ok).status_code == 404
    assert client.get("/media/x/..%2F..%2Fjobs.db", headers=ok).status_code == 404


def test_web_refuses_open_host_without_password(settings):
    from videoautomation.errors import ConfigError
    from videoautomation.web.app import run

    with pytest.raises(ConfigError, match="VAUTO_WEB_PASSWORD"):
        run(settings, host="0.0.0.0")


# --------------------------------------------------------------------- CLI


def run_cli(monkeypatch, capsys, settings, *argv):
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_platforms_and_doctor(monkeypatch, capsys, settings):
    code, out, _ = run_cli(monkeypatch, capsys, settings, "platforms", "--tier", "1")
    assert code == 0 and "instagram" in out and "ready" in out
    code, out, _ = run_cli(monkeypatch, capsys, settings, "doctor", "--json")
    assert any(c["name"] == "ffmpeg" for c in json.loads(out))


@needs_ffmpeg
def test_cli_dry_run_post(monkeypatch, capsys, settings, media_dir):
    code, out, err = run_cli(monkeypatch, capsys, settings, "post", str(media_dir / "landscape.mp4"),
                             "-c", "Hello #uk", "-p", "instagram,x,lemon8", "--trial", "--at", "+2h", "--dry-run")
    assert code == 0, err
    assert "preview only" in out and "instagram trial reel" in out and "lemon8" in out
    assert "scheduled for" in out
    assert "Converting video" in err


def test_cli_jobs_cancel_and_retry(monkeypatch, capsys, settings):
    store = JobStore(settings.db_path)
    store.insert(PostJob("instagram", "trial_reel", "meta", [], "c", idem_key="abcdef123",
                         run_at=iso(utcnow() + timedelta(hours=1))))
    code, out, _ = run_cli(monkeypatch, capsys, settings, "jobs", "--cancel", "abcdef")
    assert code == 0 and "cancelled by you" in out
    assert store.get("abcdef123").status == "skipped"
    code, _, err = run_cli(monkeypatch, capsys, settings, "jobs", "--cancel", "abcdef")
    assert code == 2 and "Only queued jobs" in err


def test_cli_bad_platform(monkeypatch, capsys, settings):
    code, _, err = run_cli(monkeypatch, capsys, settings, "post", "x.mp4", "-c", "hi", "-p", "tumblr")
    assert code == 2 and "No posting route" in err


def test_web_blocks_csrf_and_rebinding(client):
    # cross-site form posts are refused
    r = client.post("/api/settings", data='{"values": {"VAUTO_WEB_PASSWORD": "x"}}',
                    headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/settings", data='{"values": {}}', headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    # a hostile domain resolving to 127.0.0.1 is refused without a password
    assert client.get("/api/status", headers={"Host": "evil.example:8765"}).status_code == 403
    assert client.post("/api/settings", json={"values": {}},
                       headers={"Origin": "http://localhost"}).status_code == 200
