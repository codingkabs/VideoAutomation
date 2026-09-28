"""My posts: history, numbers from each platform, summaries, limits, digest and saved captions."""

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from videoautomation import stats, tracker as tracker_mod
from videoautomation.config import Settings
from videoautomation.errors import VautoError
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import JobStore, iso, utcnow
from videoautomation.tracker import Tracker, compact, digest_due, digest_text, housekeeping

from .conftest import FakeResponse, FakeSession, needs_ffmpeg

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)  # a Monday, 13:00 in London


def add_job(tr: Tracker, key: str, post_id: str, platform: str, status: str, result: PostResult | None = None,
            surface: str = "reel", backend: str = "meta", run_at: datetime | None = None, caption: str = "c"):
    job = PostJob(platform, surface, backend, [MediaFile("/nope.mp4", "video")], caption, idem_key=key,
                  post_id=post_id, run_at=iso(run_at or NOW - timedelta(hours=2)))
    tr.store.insert(job, "running" if result else status)
    if result is not None:
        tr.store.finish(key, result)
    return job


def published(platform: str, surface: str = "reel", **kw) -> PostResult:
    return PostResult(platform, surface, "published", **kw)


@pytest.fixture
def tr(settings) -> Tracker:
    return Tracker.open(settings)


# ----------------------------------------------------------------- listing


def test_post_states_and_filters(settings, tr):
    add_job(tr, "a1", "postA", "instagram", "published", published("instagram", url="https://ig/a"),
            caption="Sunset run #running")
    add_job(tr, "a2", "postA", "tiktok", "handoff", PostResult("tiktok", "video", "handoff"), backend="handoff",
            surface="video", caption="Sunset run #running")
    add_job(tr, "b1", "postB", "instagram", "pending", run_at=NOW + timedelta(hours=5), caption="Tomorrow's vlog")
    add_job(tr, "c1", "postC", "youtube", "failed", PostResult("youtube", "short", "failed", error="quota"),
            backend="zernio", surface="short", caption="Broken one")
    add_job(tr, "d1", "postD", "instagram", "skipped", PostResult("instagram", "reel", "skipped",
                                                                   error="cancelled by you"), caption="Dropped")

    everything = tr.posts(settings, now=NOW)
    states = {p["post_id"]: p["state"] for p in everything["posts"]}
    assert states == {"postA": "posted", "postB": "upcoming", "postC": "failed", "postD": "cancelled"}
    assert everything["total"] == 4

    attention = {p["post_id"] for p in tr.posts(settings, show="attention", now=NOW)["posts"]}
    assert attention == {"postA", "postC"}  # a hand-off waiting for you, and a failure
    assert [p["post_id"] for p in tr.posts(settings, query="sunset", now=NOW)["posts"]] == ["postA"]
    assert {p["post_id"] for p in tr.posts(settings, platform="youtube", now=NOW)["posts"]} == {"postC"}
    upcoming = tr.posts(settings, show="upcoming", now=NOW)["posts"][0]
    assert upcoming["upcoming"][0]["platform"] == "instagram"


def test_scheduled_on_zernio_in_the_past_counts_as_posted(settings, tr):
    add_job(tr, "z1", "postZ", "tiktok", "scheduled", PostResult("tiktok", "video", "scheduled", remote_id="zp1"),
            backend="zernio", surface="video", run_at=NOW - timedelta(hours=1))
    post = tr.describe("postZ", settings=settings, now=NOW)
    assert post["state"] == "posted" and post["jobs"][0]["status"] == "scheduled_past"


def test_mark_posted_and_typed_numbers(settings, tr):
    add_job(tr, "h1", "postH", "snapchat", "handoff", PostResult("snapchat", "spotlight", "handoff"),
            backend="handoff", surface="spotlight")
    add_job(tr, "h2", "postH", "lemon8", "handoff", PostResult("lemon8", "video", "handoff"),
            backend="handoff", surface="video")
    with pytest.raises(VautoError, match="https://"):
        tr.mark_posted("h1", "snapchat.com/x")
    result = tr.mark_posted("h1", "https://www.snapchat.com/spotlight/x")
    assert result.status == "published" and tr.store.get("h1").result.url.endswith("/x")

    with pytest.raises(VautoError, match="number"):
        tr.set_numbers("h2", {"views": "lots"})
    with pytest.raises(VautoError, match="at least one"):
        tr.set_numbers("h2", {"views": ""})
    assert tr.set_numbers("h2", {"views": "1,200", "likes": 30}) == {"views": 1200.0, "likes": 30.0}
    assert tr.store.get("h2").status == "published"  # numbers mean it is live
    job = tr.describe("postH", settings=settings, now=NOW)["jobs"][1]
    assert job["metrics"]["views"] == 1200 and job["stats_source"] == "you" and not job["auto_stats"]


def test_mark_posted_refuses_queued_and_ambiguous(settings, tr):
    add_job(tr, "q1", "postQ", "instagram", "pending", run_at=NOW + timedelta(hours=1))
    add_job(tr, "q2", "postQ", "tiktok", "pending", run_at=NOW + timedelta(hours=1))
    with pytest.raises(VautoError, match="queued"):
        tr.mark_posted("q1")
    with pytest.raises(VautoError, match="several"):
        tr.find("q")
    with pytest.raises(VautoError, match="No post"):
        tr.find("zzz")


def test_latest_stats_and_history(settings, tr):
    add_job(tr, "s1", "postS", "instagram", "published", published("instagram"))
    tr.add_stats("s1", {"views": 10, "likes": 1, "bogus": 5}, at=NOW - timedelta(hours=6))
    tr.add_stats("s1", {"views": 90, "likes": 7}, at=NOW)
    latest = tr.latest_stats(["s1", "missing"])
    assert latest["s1"]["metrics"] == {"views": 90.0, "likes": 7.0} and "missing" not in latest
    assert [h["metrics"]["views"] for h in tr.history("s1")] == [10.0, 90.0]


def test_forget_removes_history_but_not_queued(settings, tr):
    add_job(tr, "f1", "postF", "instagram", "published", published("instagram"))
    tr.add_stats("f1", {"views": 5})
    add_job(tr, "g1", "postG", "instagram", "pending", run_at=NOW + timedelta(hours=1))
    with pytest.raises(VautoError, match="cancel"):
        tr.forget("postG")
    tr.forget("postF")
    assert tr.store.get("f1") is None and tr.latest_stats(["f1"]) == {}


# ---------------------------------------------------------------- summary


def test_summary_totals_platforms_top_hours_and_tags(settings, tr):
    for i, (views, hour) in enumerate([(1000, 8), (5000, 18), (3000, 18), (200, 8)]):
        at = (NOW - timedelta(days=i)).replace(hour=hour)
        add_job(tr, f"ig{i}", f"post{i}", "instagram", "published", published("instagram", url=f"https://ig/{i}"),
                run_at=at, caption=f"clip {i} #uk" + (" #viral" if views > 2000 else ""))
        tr.add_stats(f"ig{i}", {"views": views, "likes": views / 10}, at=NOW)
    add_job(tr, "tt0", "post0", "tiktok", "published", published("tiktok", "video"), surface="video",
            backend="zernio", run_at=(NOW).replace(hour=8))
    tr.add_stats("tt0", {"views": 7000, "likes": 100}, at=NOW)
    add_job(tr, "old", "postOld", "instagram", "published", published("instagram"), run_at=NOW - timedelta(days=60))
    tr.add_stats("old", {"views": 99999}, at=NOW)
    add_job(tr, "bad", "postBad", "youtube", "failed", PostResult("youtube", "short", "failed", error="x"),
            backend="zernio", surface="short", run_at=NOW - timedelta(hours=1))

    s = tr.summary(settings, days=30, now=NOW)
    assert s["posts"] == 5 and s["live"] == 5 and s["failed"] == 1
    assert s["totals"]["views"] == 16200  # the 60-day-old post is outside the window
    assert [p["name"] for p in s["platforms"]] == ["Instagram", "TikTok"]
    ig = s["platforms"][0]
    assert ig["posts"] == 4 and ig["avg_views"] == 2300 and ig["best"]["views"] == 5000
    assert s["top"][0]["post_id"] == "post0" and s["top"][0]["totals"]["views"] == 8000
    hours = {h["hour"]: h for h in s["by_hour"]}
    assert set(hours) == {9, 19}  # BST local hours
    assert s["enough_data"] and s["has_numbers"]
    tags = {t["tag"]: t for t in s["hashtags"]}
    assert tags["#viral"]["posts"] == 2 and tags["#uk"]["posts"] == 4
    assert tags["#uk"]["avg_views"] == 4050 and tags["#viral"]["avg_views"] == 4000
    assert [t["tag"] for t in s["hashtags"]] == ["#uk", "#viral"]  # both used on several posts

    everything = tr.summary(settings, days=None, now=NOW)
    assert everything["totals"]["views"] == 16200 + 99999


def test_summary_calendar_streak_and_upcoming(settings, tr):
    for d in range(3):
        add_job(tr, f"k{d}", f"p{d}", "instagram", "published", published("instagram"),
                run_at=NOW - timedelta(days=d))
    add_job(tr, "gap", "pgap", "instagram", "published", published("instagram"), run_at=NOW - timedelta(days=5))
    add_job(tr, "soon", "psoon", "tiktok", "pending", run_at=NOW + timedelta(days=1), caption="next one")
    s = tr.summary(settings, days=30, now=NOW)
    assert s["streak"] == 3 and sum(s["calendar"]["days"].values()) == 4
    assert s["upcoming"][0]["caption"] == "next one" and not s["enough_data"]


def test_limit_notes(settings, tr):
    for i in range(14):
        add_job(tr, f"t{i}", f"p{i}", "tiktok", "published", published("tiktok", "video"), surface="video",
                backend="zernio", run_at=NOW - timedelta(hours=i))
    add_job(tr, "old", "pold", "tiktok", "published", published("tiktok", "video"), surface="video",
            backend="zernio", run_at=NOW - timedelta(hours=30))
    notes = tr.limit_notes(["tiktok", "instagram", "bluesky"], now=NOW)
    assert notes == ["TikTok: 14 of 15 daily posts used"]
    add_job(tr, "t15", "p15", "tiktok", "published", published("tiktok", "video"), surface="video",
            backend="zernio", run_at=NOW)
    assert "may be refused" in tr.limit_notes(["tiktok"], now=NOW)[0]


def test_export_csv(settings, tr):
    add_job(tr, "e1", "postE", "instagram", "published", published("instagram", url="https://ig/e"),
            caption='Quote "this", please')
    tr.add_stats("e1", {"views": 1234.0, "likes": 5})
    tr.conn.execute("INSERT INTO posts (post_id, kind, caption, platforms, rejected, created_at, updated_at)"
                    " VALUES ('postE', 'video', 'Quote \"this\", please', '[\"instagram\",\"youtube\"]', ?, ?, ?)",
                    (json.dumps([{"platform": "youtube", "surface": "short", "error": "too long"}]),
                     iso(NOW), iso(NOW)))
    rows = list(csv.DictReader(io.StringIO(tr.export_csv(settings))))
    assert rows[0]["platform"] == "Instagram" and rows[0]["views"] == "1234" and rows[0]["link"] == "https://ig/e"
    assert rows[0]["caption"] == 'Quote "this", please'
    assert rows[1]["status"] == "not posted" and rows[1]["error"] == "too long"


def test_snippets(tr):
    with pytest.raises(VautoError):
        tr.add_snippet(" ", "x")
    a = tr.add_snippet("Sign-off", "Follow for more 🙌")
    tr.add_snippet("Gym tags", "#gym #fitness")
    again = tr.add_snippet("Sign-off", "Follow for daily clips")
    assert again["id"] == a["id"]
    assert [s["name"] for s in tr.snippets()] == ["Gym tags", "Sign-off"]
    assert tr.snippets()[1]["text"] == "Follow for daily clips"
    tr.delete_snippet(a["id"])
    assert [s["name"] for s in tr.snippets()] == ["Gym tags"]


def test_compact_numbers():
    assert [compact(n) for n in (0, 999, 1000, 12345, 1_500_000, 2e9)] == ["0", "999", "1k", "12.3k", "1.5M", "2B"]


# ---------------------------------------------------------- stats readers


def test_facebook_reel_and_photo_numbers(settings):
    job = PostJob("facebook", "reel", "meta", [], "c")
    insights = {"data": [
        {"name": "blue_reels_play_count", "values": [{"value": 4200}]},
        {"name": "post_impressions_unique", "values": [{"value": 3900}]},
        {"name": "post_video_likes_by_reaction_type", "values": [{"value": {"REACTION_LIKE": 40, "REACTION_LOVE": 2}}]},
        {"name": "post_video_social_actions", "values": [{"value": {"SHARE": 7, "COMMENT": 3}}]},
    ]}
    session = FakeSession({("GET", "/vid1/video_insights"): FakeResponse(data=insights)})
    got = stats.facebook_metrics(settings, job, PostResult("facebook", "reel", "published", remote_id="vid1"), session)
    assert got == {"views": 4200, "reach": 3900, "likes": 42, "comments": 3, "shares": 7}

    photo = PostJob("facebook", "photos", "meta", [], "c")
    session = FakeSession({("GET", "/page_post1"): FakeResponse(data={
        "shares": {"count": 2}, "reactions": {"summary": {"total_count": 11}}, "comments": {"summary": {"total_count": 4}}})})
    got = stats.facebook_metrics(settings, photo, PostResult("facebook", "photos", "published", remote_id="page_post1"),
                                 session)
    assert got == {"likes": 11, "comments": 4, "shares": 2}


def test_threads_bluesky_mastodon_numbers(settings):
    settings.threads_access_token = "th"
    session = FakeSession({
        ("GET", "refresh_access_token"): FakeResponse(data={"access_token": "th", "expires_in": 5184000}),
        ("GET", "graph.threads.net/v1.0/t1/insights"): FakeResponse(data={"data": [
        {"name": "views", "values": [{"value": 800}]}, {"name": "likes", "values": [{"value": 20}]},
        {"name": "replies", "values": [{"value": 4}]}, {"name": "reposts", "values": [{"value": 2}]},
        {"name": "quotes", "values": [{"value": 1}]}]})})
    assert stats.threads_metrics(settings, "t1", session) == {"views": 800, "likes": 20, "comments": 4, "shares": 3}

    session = FakeSession({("GET", "app.bsky.feed.getPosts"): FakeResponse(data={"posts": [
        {"likeCount": 9, "replyCount": 2, "repostCount": 3, "quoteCount": 1}]})})
    assert stats.bluesky_metrics("at://did/app.bsky.feed.post/1", session) == {"likes": 9, "comments": 2, "shares": 4}
    assert session.calls[0][2]["params"]["uris"] == "at://did/app.bsky.feed.post/1"

    settings.mastodon_instance, settings.mastodon_access_token = "https://mstdn.social", "tok"
    session = FakeSession({("GET", "/api/v1/statuses/55"): FakeResponse(data={
        "favourites_count": 6, "replies_count": 1, "reblogs_count": 2})})
    assert stats.mastodon_metrics(settings, "55", session) == {"likes": 6, "comments": 1, "shares": 2}
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer tok"


def test_zernio_numbers_pick_the_platform_and_use_impressions(settings):
    data = {"postId": "zp", "analytics": {"views": 1}, "platformAnalytics": [
        {"platform": "tiktok", "analytics": {"views": 5000, "likes": 300, "comments": 12, "shares": 40, "saves": 9}},
        {"platform": "linkedin", "analytics": {"impressions": 700, "likes": 5}}]}
    session = FakeSession({("GET", "/analytics"): FakeResponse(data=data)})
    assert stats.zernio_metrics(settings, "zp", "tiktok", session) == {
        "views": 5000, "likes": 300, "comments": 12, "shares": 40, "saved": 9}
    assert stats.zernio_metrics(settings, "zp", "linkedin", session) == {"views": 700, "likes": 5}
    assert session.calls[0][2]["params"] == {"postId": "zp"}


def test_reader_choice(settings):
    ig_zernio = PostJob("instagram", "reel", "zernio", [], "c")
    assert stats.reader_for(settings, ig_zernio, published("instagram", platform_post_id="m1")) == "instagram"
    assert stats.reader_for(settings, ig_zernio, published("instagram", remote_id="zp")) == "zernio"
    assert stats.reader_for(settings, PostJob("tiktok", "video", "handoff", [], "c"), published("tiktok")) is None
    assert stats.reader_for(settings, PostJob("instagram", "story", "meta", [], "c"),
                            published("instagram", remote_id="s")) is None
    with pytest.raises(stats.NoStats):
        stats.fetch(settings, PostJob("lemon8", "video", "browser", [], "c"), published("lemon8"), FakeSession({}))


def test_refresh_updates_confirms_and_reports_problems(settings, tr):
    add_job(tr, "ig", "p1", "instagram", "published", published("instagram", platform_post_id="m1"))
    add_job(tr, "tt", "p1", "tiktok", "scheduled", PostResult("tiktok", "video", "scheduled", remote_id="zt"),
            backend="zernio", surface="video", run_at=NOW - timedelta(hours=1))
    add_job(tr, "yt", "p1", "youtube", "published", published("youtube", "short", remote_id="zy"),
            backend="zernio", surface="short")
    add_job(tr, "hand", "p1", "snapchat", "published", published("snapchat", "spotlight"), backend="handoff",
            surface="spotlight")
    add_job(tr, "old", "p0", "instagram", "published", published("instagram", platform_post_id="m0"),
            run_at=NOW - timedelta(days=90))

    def analytics(url, kwargs):
        if kwargs["params"]["postId"] == "zy":
            return FakeResponse(500, {"error": "analytics add-on required"})
        return FakeResponse(data={"platformAnalytics": [{"platform": "tiktok", "analytics": {"views": 321}}]})

    session = FakeSession({
        ("GET", "/m1/insights"): FakeResponse(data={"data": [{"name": "views", "values": [{"value": 55}]}]}),
        ("GET", "/posts/zt"): FakeResponse(data={"post": {"platforms": [
            {"platform": "tiktok", "status": "published", "publishedUrl": "https://tiktok.com/@me/video/1",
             "platformPostId": "tt1"}]}}),
        ("GET", "/analytics"): analytics,
    })
    report = stats.refresh(settings, tr, session=session, now=NOW)
    assert report.updated == 2 and report.confirmed == 1 and report.skipped == 1
    assert len(report.errors) == 1 and "youtube" in report.errors[0]
    row = tr.store.get("tt")
    assert row.status == "published" and row.result.url == "https://tiktok.com/@me/video/1"
    assert tr.latest_stats(["ig", "tt"])["tt"]["metrics"] == {"views": 321.0}
    assert "old" not in tr.latest_stats(["old"])
    assert tr.get_meta("stats_refreshed_at") == iso(NOW)
    assert "updated numbers for 2" in report.text()


# ------------------------------------------------------ worker and digest


def _with(settings: Settings, **changes) -> Settings:
    for key, value in changes.items():
        setattr(settings, key, value)
    return settings


def test_digest_due_rules(settings, tr):
    monday_morning = datetime(2026, 9, 28, 7, 30, tzinfo=timezone.utc)  # 08:30 London
    assert not digest_due(settings, tr, monday_morning)
    assert digest_due(settings, tr, NOW)
    tracker_mod.mark_digest_sent(settings, tr, NOW)
    assert not digest_due(settings, tr, NOW + timedelta(hours=3))
    assert not digest_due(settings, tr, NOW + timedelta(days=1))  # Tuesday
    assert digest_due(settings, tr, NOW + timedelta(days=7))
    _with(settings, digest="daily")
    assert digest_due(settings, tr, NOW + timedelta(days=1))
    _with(settings, digest="off")
    assert not digest_due(settings, tr, NOW + timedelta(days=7))


def test_digest_text(settings, tr):
    add_job(tr, "a", "pa", "instagram", "published", published("instagram", url="https://ig/a"),
            caption="Best clip ever #uk")
    tr.add_stats("a", {"views": 12345, "likes": 600, "comments": 20, "shares": 5})
    text = digest_text(settings, tr, now=NOW)
    assert "1 post(s)" in text and "12.3k views" in text and "https://ig/a" in text and "Instagram" in text
    empty = Tracker.open(_with(settings, home=settings.home / "other"))
    assert "0 post(s)" in digest_text(settings, empty, now=NOW)


def test_housekeeping_refreshes_then_waits_and_sends_digest_once(settings, tr):
    _with(settings, telegram_bot_token="bot", telegram_owner_chat_id="42", stats_refresh_hours=6)
    add_job(tr, "ig", "p1", "instagram", "published", published("instagram", platform_post_id="m1"))
    session = FakeSession({
        ("GET", "/m1/insights"): FakeResponse(data={"data": [{"name": "views", "values": [{"value": 9}]}]}),
        ("POST", "/sendMessage"): FakeResponse(data={"ok": True, "result": {"message_id": 1}}),
    })
    done = housekeeping(settings, tr, now=NOW, session=session)
    assert done[0].startswith("stats: updated numbers for 1") and done[1] == "digest sent"
    sent = [c for c in session.calls if c[1].endswith("/sendMessage")]
    assert len(sent) == 1 and "Your last 7 days" in json.dumps(sent[0][2], ensure_ascii=False)
    assert housekeeping(settings, tr, now=NOW + timedelta(hours=1), session=session) == []
    later = housekeeping(settings, tr, now=NOW + timedelta(hours=7), session=session)
    assert len(later) == 1 and later[0].startswith("stats:")


def test_housekeeping_off(settings, tr):
    _with(settings, stats_refresh_hours=0)
    add_job(tr, "ig", "p1", "instagram", "published", published("instagram", platform_post_id="m1"))
    assert housekeeping(settings, tr, now=NOW, session=FakeSession({})) == []


def test_settings_validation():
    base = {"VAUTO_HOME": "/tmp/x"}
    assert Settings.from_env(env=base, dotenv=None).digest == "weekly"
    with pytest.raises(VautoError, match="VAUTO_DIGEST"):
        Settings.from_env(env={**base, "VAUTO_DIGEST": "hourly"}, dotenv=None)
    with pytest.raises(VautoError, match="VAUTO_STATS_REFRESH_HOURS"):
        Settings.from_env(env={**base, "VAUTO_STATS_REFRESH_HOURS": "six"}, dotenv=None)


# ------------------------------------------------------- pipeline hook-up


@needs_ffmpeg
def test_real_post_is_recorded_with_thumbnail_and_dry_run_is_not(settings, media_dir):
    from videoautomation import service

    settings.backends.update(instagram="handoff", tiktok="handoff")
    req = service.make_request(settings, [media_dir / "landscape.mp4"], "Gym day #gym", ["instagram", "tiktok"],
                               dry_run=True)
    service.post(settings, req)
    assert not settings.db_path.is_file() or Tracker.open(settings).posts(settings)["total"] == 0

    req = service.make_request(settings, [media_dir / "landscape.mp4"], "Gym day #gym", ["instagram", "tiktok"])
    plan, results = service.post(settings, req)
    assert {r.status for r in results} == {"handoff"}
    tr = Tracker.open(settings)
    post = tr.posts(settings)["posts"][0]
    assert post["post_id"] == plan.post_id and post["caption"] == "Gym day #gym" and post["kind"] == "video"
    assert post["thumb"] and post["thumb"].endswith("thumb.jpg") and post["state"] == "waiting"
    assert post["inputs"] == [str(media_dir / "landscape.mp4")]


@needs_ffmpeg
def test_plan_warns_near_daily_limit(settings, media_dir):
    from videoautomation import service

    tr = Tracker.open(settings)
    for i in range(15):
        add_job(tr, f"t{i}", f"p{i}", "tiktok", "published", published("tiktok", "video"), surface="video",
                backend="zernio", run_at=utcnow() - timedelta(hours=1))
    req = service.make_request(settings, [media_dir / "vertical_silent.mp4"], "hi", ["tiktok"], dry_run=True)
    plan, _ = service.post(settings, req)
    assert any("TikTok: 15 posts" in n and "may be refused" in n for n in plan.notes)


# ------------------------------------------------------------ web, CLI, bot


@pytest.fixture
def web(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.dotenv_path = tmp_path / ".env"
    app = create_app(settings)
    app.config["TESTING"] = True
    return app.test_client()


def _seed(settings):
    tr = Tracker.open(settings)
    work = settings.renders_dir / "postW"
    work.mkdir(parents=True, exist_ok=True)
    (work / "thumb.jpg").write_bytes(b"jpg")
    tr.conn.execute("INSERT INTO posts (post_id, kind, caption, platforms, inputs, thumb, created_at, updated_at)"
                    " VALUES ('postW', 'video', 'Web test #uk', '[\"instagram\",\"snapchat\"]', '[\"/secret/path.mp4\"]',"
                    " ?, ?, ?)", (str(work / "thumb.jpg"), iso(utcnow()), iso(utcnow())))
    add_job(tr, "webig000001", "postW", "instagram", "published", published("instagram", url="https://ig/w"),
            run_at=utcnow() - timedelta(hours=1), caption="Web test #uk")
    add_job(tr, "websnap0001", "postW", "snapchat", "handoff", PostResult("snapchat", "spotlight", "handoff"),
            backend="handoff", surface="spotlight", run_at=utcnow() - timedelta(hours=1), caption="Web test #uk")
    tr.add_stats("webig000001", {"views": 2500, "likes": 100})
    return tr


def test_web_posts_detail_actions_and_privacy(web, settings):
    _seed(settings)
    data = web.get("/api/posts").get_json()
    post = data["posts"][0]
    assert data["total"] == 1 and post["thumb"] == "/media/postW/thumb.jpg" and "inputs" not in post
    assert web.get(post["thumb"]).status_code == 200
    assert web.get("/api/posts?show=attention").get_json()["total"] == 1
    assert web.get("/api/posts?q=nothing").get_json()["total"] == 0

    detail = web.get("/api/posts/postW").get_json()
    assert detail["jobs"][0]["history"][0]["metrics"]["views"] == 2500

    # JSON only for writes (CSRF), and friendly errors
    assert web.post("/api/jobs/websnap/posted", data="url=x").status_code == 415
    bad = web.post("/api/jobs/websnap/posted", json={"url": "nope"})
    assert bad.status_code == 400 and "https://" in bad.get_json()["error"]
    assert web.post("/api/jobs/websnap/posted", json={"url": "https://snapchat.com/s/1"}).get_json()["status"] == "published"
    assert web.post("/api/jobs/webig0/numbers", json={"views": "3000"}).get_json()["metrics"] == {"views": 3000.0}
    assert web.get("/api/posts?show=attention").get_json()["total"] == 0

    summary = web.get("/api/summary?days=7").get_json()
    assert summary["posts"] == 1 and summary["totals"]["views"] == 3000 and summary["timezone"] == "Europe/London"
    csv_resp = web.get("/api/export.csv")
    assert csv_resp.mimetype == "text/csv" and "attachment" in csv_resp.headers["Content-Disposition"]
    assert "https://snapchat.com/s/1" in csv_resp.get_data(as_text=True)

    assert web.delete("/api/posts/postW").get_json()["ok"]
    assert web.get("/api/posts").get_json()["total"] == 0


def test_web_refresh_runs_in_background(web, settings, monkeypatch):
    import time

    _seed(settings)
    monkeypatch.setattr(stats, "refresh", lambda s, t, **kw: stats.RefreshReport(updated=1, skipped=1))
    task = web.post("/api/stats/refresh", json={"days": 7}).get_json()
    for _ in range(50):
        state = web.get(f"/api/tasks/{task['task_id']}").get_json()
        if state["state"] != "running":
            break
        time.sleep(0.05)
    assert state["state"] == "done" and state["result"]["updated"] == 1
    assert web.post("/api/posts/postW/refresh", json={}).status_code == 200


@needs_ffmpeg
def test_web_post_again_reuses_the_video(web, settings, media_dir):
    tr = _seed(settings)
    tr.conn.execute("UPDATE posts SET inputs = ? WHERE post_id = 'postW'", (json.dumps([str(media_dir / "landscape.mp4")]),))
    data = web.post("/api/posts/postW/reuse", json={}).get_json()
    assert data["caption"] == "Web test #uk" and data["files"][0]["kind"] == "video"
    tr.conn.execute("UPDATE posts SET inputs = '[\"/gone.mp4\"]' WHERE post_id = 'postW'")
    missing = web.post("/api/posts/postW/reuse", json={})
    assert missing.status_code == 400 and "gone" in missing.get_json()["error"]


def test_web_snippets(web):
    assert web.get("/api/snippets").get_json() == []
    made = web.post("/api/snippets", json={"name": "Tags", "text": "#uk #fyp"}).get_json()
    assert web.get("/api/snippets").get_json()[0]["text"] == "#uk #fyp"
    assert web.post("/api/snippets", json={"name": "", "text": "x"}).status_code == 400
    web.delete(f"/api/snippets/{made['id']}")
    assert web.get("/api/snippets").get_json() == []


def test_web_page_has_new_sections(web):
    html = web.get("/").get_data(as_text=True)
    for anchor in ('id="tab-posts"', 'id="tab-stats"', 'id="snippet-chips"', 'id="viz-tip"', 'data-tab="stats"'):
        assert anchor in html


def _cli(monkeypatch, capsys, settings, *argv):
    from videoautomation import cli

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


def test_cli_posts_stats_export(monkeypatch, capsys, settings, tmp_path):
    code, out = _cli(monkeypatch, capsys, settings, "posts")
    assert code == 0 and "No posts yet" in out
    _seed(settings)
    code, out = _cli(monkeypatch, capsys, settings, "posts")
    assert code == 0 and "Web test #uk" in out and "✅ instagram" in out and "📲 snapchat" in out and "2.5k" in out
    code, out = _cli(monkeypatch, capsys, settings, "posts", "postW")
    assert "websnap000" in out and "https://ig/w" in out
    code, out = _cli(monkeypatch, capsys, settings, "posts", "--mark-posted", "websnap", "--url", "https://snap/1")
    assert code == 0 and "Marked snapchat" in out
    code, out = _cli(monkeypatch, capsys, settings, "posts", "--show", "attention")
    assert "No posts match" in out
    code, out = _cli(monkeypatch, capsys, settings, "stats", "--days", "7")
    assert code == 0 and "1 post(s)" in out and "2.5k views" in out and "Instagram" in out
    code, out = _cli(monkeypatch, capsys, settings, "stats", "--json")
    assert json.loads(out)["totals"]["views"] == 2500
    dest = tmp_path / "out.csv"
    code, out = _cli(monkeypatch, capsys, settings, "export", str(dest))
    assert code == 0 and dest.read_text().startswith("post_id,posted_at,platform")


def test_bot_posts_and_stats(settings, monkeypatch):
    from videoautomation.bot import Bot

    from .test_apps import FakeTelegram, InlineExecutor

    api = FakeTelegram()
    settings.telegram_bot_token, settings.telegram_allowed_user_ids = "T", [7]
    bot = Bot(settings, api=api, executor=InlineExecutor(), api_factory=lambda: api)
    bot.handle_command(55, "/posts")
    assert "No posts yet" in api.sent[-1]["text"]
    _seed(settings)
    bot.handle_command(55, "/posts")
    text = api.sent[-1]["text"]
    assert "Web test #uk" in text and "2.5k views" in text and "✅ Instagram: https://ig/w" in text
    monkeypatch.setattr(stats, "refresh", lambda *a, **k: stats.RefreshReport())
    bot.handle_command(55, "/stats")
    assert "Your last 7 days" in api.sent[-1]["text"]
    bot.handle_command(55, "/help")
    assert "/posts" in api.sent[-1]["text"]
