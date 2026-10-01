"""Save as drafts: native drafts where the platform allows it, held in vauto everywhere else."""

import os
import time
from datetime import timedelta

import pytest

from videoautomation import pipeline, service
from videoautomation.config import Settings
from videoautomation.errors import VautoError
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.pipeline import PostRequest, build_plan, execute
from videoautomation.publishers.zernio import ZernioPublisher
from videoautomation.scheduler import JobStore, iso, parse_iso, utcnow
from videoautomation.tracker import Tracker, cleanup

from .conftest import needs_ffmpeg

PLATFORMS = ["instagram", "facebook", "tiktok", "youtube"]


class Recorder:
    remote_schedule = False

    def __init__(self):
        self.jobs = []

    def publish(self, job):
        self.jobs.append(job)
        status = "draft" if job.options.get("draft") else "published"
        return PostResult(job.platform, job.surface, status, url=f"https://{job.platform}.example/{job.surface}")

    def schedules_remotely(self, job):
        return False


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    from videoautomation.publishers import DryRunPublisher

    def factory(backend, platform, settings, storage, dry_run=False):
        return DryRunPublisher(settings, storage) if dry_run else rec

    monkeypatch.setattr(pipeline, "make_publisher", factory)
    monkeypatch.setattr(service, "publisher_factory", lambda s: (lambda job: rec))
    return rec


def _request(media_dir, **kw):
    base = dict(inputs=[media_dir / "vertical_silent.mp4"], caption="Draft test #uk", platforms=PLATFORMS,
                drafts=True, trial=True)
    base.update(kw)
    return PostRequest(**base)


def _by(jobs):
    return {(j.platform, j.surface): j for j in jobs}


@needs_ffmpeg
def test_plan_uses_native_drafts_where_possible_and_holds_the_rest(media_dir, settings):
    plan = build_plan(_request(media_dir), settings)
    jobs = _by(plan.jobs)
    assert jobs[("tiktok", "video")].options["draft"] is True
    youtube = jobs[("youtube", "short")].options
    assert youtube["draft"] is True and youtube["visibility"] == "private"
    for key in [("instagram", "reel"), ("facebook", "reel"), ("instagram", "trial_reel"), ("instagram", "trial_report")]:
        assert jobs[key].options.get("hold") is True, key
    note = plan.notes[0]
    assert note.startswith("drafts: ") and "YouTube Shorts uploaded as Private" in note and "TikTok drafts" in note
    assert "Instagram" in note and "Post drafts" in note


@needs_ffmpeg
def test_without_drafts_nothing_is_held(media_dir, settings):
    plan = build_plan(_request(media_dir, drafts=False), settings)
    assert not any(j.options.get("hold") for j in plan.jobs)
    assert _by(plan.jobs)[("youtube", "short")].options["visibility"] == "public"


@needs_ffmpeg
def test_scheduled_time_is_ignored_for_drafts(media_dir, settings):
    later = iso(utcnow() + timedelta(hours=5))
    plan = build_plan(_request(media_dir, publish_at=later, trial=False), settings)
    assert all(j.run_at is None for j in plan.jobs)
    assert "scheduled time was ignored" in plan.notes[0]


@needs_ffmpeg
def test_saving_drafts_then_posting_them(media_dir, settings, recorder):
    req = _request(media_dir)
    plan = build_plan(req, settings)
    results = {(r.platform, r.surface): r for r in execute(plan, settings, req)}
    assert results[("tiktok", "video")].status == "draft" and results[("youtube", "short")].status == "draft"
    assert results[("instagram", "reel")].status == "held" and results[("facebook", "reel")].status == "held"
    assert results[("instagram", "trial_reel")].status == "held"
    assert ("instagram", "trial_report") not in results
    published = {(j.platform, j.surface) for j in recorder.jobs}
    assert published == {("tiktok", "video"), ("youtube", "short")}  # nothing public yet

    store = JobStore(settings.db_path)
    post = Tracker(store).describe(plan.post_id, settings=settings)
    assert post["drafts"] == 3 and post["needs_you"] and post["state"] == "waiting"

    # Posting the same video again is caught as a duplicate, not re-drafted.
    again = execute(build_plan(_request(media_dir), settings), settings, _request(media_dir))
    assert {r.status for r in again} == {"duplicate"}

    # The trial cannot go before its main Reel.
    trial_key = next(r.idem_key for r in store.rows(post_id=plan.post_id) if r.job.surface == "trial_reel")
    with pytest.raises(VautoError, match="main Reel first"):
        service.retry(settings, trial_key[:12])

    before = utcnow()
    out = {(r.platform, r.surface): r for r in service.publish_drafts(settings, plan.post_id)}
    assert out[("instagram", "reel")].status == "published" and out[("facebook", "reel")].status == "published"
    assert out[("instagram", "trial_reel")].status == "queued"
    rows = {(r.job.platform, r.job.surface): r for r in store.rows(post_id=plan.post_id)}
    trial, report = rows[("instagram", "trial_reel")], rows[("instagram", "trial_report")]
    assert trial.status == "pending" and report.status == "pending"
    delay = parse_iso(trial.run_at) - before
    assert timedelta(minutes=59) <= delay <= timedelta(minutes=121)
    assert parse_iso(report.run_at) - parse_iso(trial.run_at) == timedelta(hours=settings.trial_report_hours)
    assert "hold" not in trial.job.options and "hold" not in rows[("instagram", "reel")].job.options
    assert Tracker(store).describe(plan.post_id, settings=settings)["drafts"] == 0
    with pytest.raises(VautoError, match="no drafts"):
        service.publish_drafts(settings, plan.post_id)


def _held(settings, key, platform="instagram", surface="reel", depends_on=None):
    store = JobStore(settings.db_path)
    job = PostJob(platform, surface, "zernio", [MediaFile("/x.mp4", "video")], "c", options={"hold": True},
                  idem_key=key, post_id="heldpost", depends_on=depends_on)
    store.insert(job, "held")
    return store


def test_discard_and_post_one_draft(settings, recorder):
    store = _held(settings, "heldmain01")
    _held(settings, "heldother1", platform="linkedin", surface="video")
    discarded = service.cancel(settings, "heldother")
    assert discarded.status == "skipped" and "discarded" in discarded.error
    result = service.retry(settings, "heldmain")
    assert result.status == "published" and store.get("heldmain01").status == "published"
    assert "hold" not in recorder.jobs[-1].options


def test_worker_waits_for_a_held_parent(settings, recorder):
    from videoautomation.scheduler import run_due

    store = _held(settings, "parent0001")
    trial = PostJob("instagram", "trial_reel", "zernio", [], "c", idem_key="child00001", post_id="heldpost",
                    depends_on="parent0001", run_at=iso(utcnow() - timedelta(minutes=1)))
    store.insert(trial, "pending")
    run_due(store, lambda job: recorder)
    assert store.get("child00001").status == "pending" and not recorder.jobs


def test_drafts_keep_their_files_during_cleanup(settings):
    _held(settings, "keepme0001")
    video = settings.renders_dir / "heldpost" / "master_auto.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * 100)
    old = time.time() - 30 * 86400
    os.utime(video, (old, old))
    cleanup(settings, Tracker.open(settings), days=14)
    assert video.exists()


def test_youtube_private_draft_note(settings):
    pub = ZernioPublisher(settings)
    job = PostJob("youtube", "short", "zernio", [], "c", options={"draft": True, "visibility": "private"})
    assert pub.platform_data(job)["visibility"] == "private"
    post = {"platforms": [{"platform": "youtube", "status": "published", "publishedUrl": "https://youtu.be/x"}]}
    result = pub._await(job, "zp1", post, [])
    assert result.status == "draft" and "Private" in result.notes[0] and result.url == "https://youtu.be/x"


def test_drafts_default_setting(settings, media_dir):
    s = Settings.from_env(env={"VAUTO_HOME": str(settings.home), "VAUTO_DRAFTS_DEFAULT": "true"}, dotenv=None)
    assert s.drafts_default is True
    req = service.make_request(s, [media_dir / "vertical_silent.mp4"], "c", ["tiktok"])
    assert req.drafts is True
    req = service.make_request(s, [media_dir / "vertical_silent.mp4"], "c", ["tiktok"], drafts=False)
    assert req.drafts is False


# ------------------------------------------------------------------ web / CLI / bot


def test_web_status_page_and_publish_endpoint(settings, tmp_path, recorder):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.dotenv_path = tmp_path / ".env"
    client = create_app(settings).test_client()
    assert client.get("/api/status").get_json()["defaults"]["drafts"] is False
    assert 'id="opt-drafts"' in client.get("/").get_data(as_text=True)
    _held(settings, "webheld001")
    post = client.get("/api/posts").get_json()["posts"][0]
    assert post["drafts"] == 1 and post["jobs"][0]["status"] == "held"
    task = client.post("/api/posts/heldpost/publish", json={}).get_json()
    for _ in range(100):
        state = client.get(f"/api/tasks/{task['task_id']}").get_json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["result"]["results"][0]["status"] == "published"


@needs_ffmpeg
def test_cli_drafts_preview_and_publish(monkeypatch, capsys, settings, media_dir, recorder):
    from videoautomation import cli

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    code = cli.main(["post", str(media_dir / "vertical_silent.mp4"), "-c", "hi", "-p", "instagram,tiktok",
                     "--drafts", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "drafts: TikTok to your TikTok drafts" in out and "hold=True" in out
    _held(settings, "clihold001")
    assert cli.main(["posts", "--publish", "heldpost"]) == 0
    assert "POSTED" in capsys.readouterr().out


def test_bot_drafts_button(settings, monkeypatch):
    from videoautomation.bot import Bot

    from .test_apps import FakeTelegram, InlineExecutor

    api = FakeTelegram()
    settings.telegram_bot_token, settings.telegram_allowed_user_ids = "T", [7]
    bot = Bot(settings, api=api, executor=InlineExecutor(), api_factory=lambda: api)
    calls = []
    monkeypatch.setattr(service, "post", lambda s, req: calls.append(req) or (
        type("P", (), {"post_id": "p", "notes": []})(), [PostResult("instagram", "reel", "held")]))
    bot.handle_update({"update_id": 1, "message": {"chat": {"id": 55}, "from": {"id": 7},
                                                   "video": {"file_id": "F", "file_size": 10}, "caption": "hi"}})
    session = bot.sessions[55]

    def press(data):
        bot.handle_update({"update_id": 2, "callback_query": {"id": "q", "from": {"id": 7}, "data": data,
                                                              "message": {"chat": {"id": 55}, "message_id": session.panel_id}}})

    press("drafts")
    assert session.drafts and "Save as drafts: on" in api.edits[-1]["text"]
    assert any(b["text"] == "💾 Save drafts" for row in api.edits[-1]["markup"]["inline_keyboard"] for b in row)
    press("post")
    assert calls[-1].drafts is True and not calls[-1].dry_run


# ------------------------------------------------------------------ per-platform drafts


@needs_ffmpeg
def test_draft_only_instagram_posts_the_rest(media_dir, settings):
    later = iso(utcnow() + timedelta(hours=5))
    plan = build_plan(_request(media_dir, draft_platforms=["instagram"], publish_at=later), settings)
    jobs = _by(plan.jobs)
    assert jobs[("instagram", "reel")].options.get("hold") and jobs[("instagram", "trial_reel")].options.get("hold")
    for key in [("facebook", "reel"), ("tiktok", "video"), ("youtube", "short")]:
        assert not jobs[key].options.get("hold") and not jobs[key].options.get("draft"), key
        assert jobs[key].run_at == later  # the rest keep their schedule
    assert "post it yourself from the app" in plan.notes[0] and "TikTok" not in plan.notes[0]


@needs_ffmpeg
def test_draft_platforms_not_in_the_post_means_no_drafts(media_dir, settings):
    plan = build_plan(_request(media_dir, draft_platforms=["snapchat"], trial=False), settings)
    assert not any(j.options.get("hold") or j.options.get("draft") for j in plan.jobs)


def test_draft_platforms_setting_and_cli(settings, media_dir, monkeypatch, capsys):
    s = Settings.from_env(env={"VAUTO_HOME": str(settings.home), "VAUTO_DRAFTS_DEFAULT": "true",
                               "VAUTO_DRAFT_PLATFORMS": "Instagram, facebook"}, dotenv=None)
    assert s.draft_platforms == ["instagram", "facebook"]
    req = service.make_request(s, [media_dir / "vertical_silent.mp4"], "c", ["instagram", "tiktok"])
    assert req.drafts and req.draft_platforms == ["instagram", "facebook"]

    from videoautomation import cli

    assert cli._draft_opts(None, "instagram,TikTok") == {"drafts": True, "draft_platforms": ["instagram", "tiktok"]}
    assert cli._draft_opts(True, None) == {"drafts": True, "draft_platforms": []}  # --drafts: every platform
    assert cli._draft_opts(False, None) == {"drafts": False, "draft_platforms": None}
    assert cli._draft_opts(None, None) == {}


def test_my_posts_carries_what_you_need_to_post_it_yourself(settings):
    store = JobStore(settings.db_path)
    job = PostJob("instagram", "reel", "zernio", [MediaFile("/x.mp4", "video")], "My caption",
                  options={"hold": True, "first_comment": "First!"}, idem_key="yourself01", post_id="yp")
    store.insert(job, "held")
    j = Tracker(store).describe("yp", settings=settings)["jobs"][0]
    assert j["caption"] == "My caption" and j["first_comment"] == "First!" and j["status"] == "held"


def test_web_defaults_include_draft_platforms(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.draft_platforms = ["instagram"]
    settings.dotenv_path = tmp_path / ".env"
    client = create_app(settings).test_client()
    assert client.get("/api/status").get_json()["defaults"]["draft_platforms"] == ["instagram"]
    html = client.get("/").get_data(as_text=True)
    assert 'id="draft-chips"' in html and 'id="draft-hint"' in html
