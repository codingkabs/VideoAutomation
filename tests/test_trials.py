"""Trial Reel verdicts, the morning briefing, main-slot reminders and the daily Trial Reel cap."""

from datetime import datetime, timedelta, timezone

import pytest

from videoautomation import briefing, insights, trials
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import JobStore, iso, utcnow
from videoautomation.tracker import Tracker

from .conftest import FakeSession, needs_ffmpeg

NOW = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)  # Saturday 08:00 London


def add_trial(store, key, hours_ago, views=None, shares=0, saved=0, likes=0, caption="Trial", depends_on=None,
              status="published", url=None):
    job = PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], caption, idem_key=key,
                  post_id=f"p_{key}", run_at=iso(NOW - timedelta(hours=hours_ago)), depends_on=depends_on)
    store.insert(job, "running")
    store.finish(key, PostResult("instagram", "trial_reel", status, url=url or f"https://instagram.com/reel/{key}"))
    if views is not None:
        Tracker(store).add_stats(key, {"views": views, "shares": shares, "saved": saved, "likes": likes})
    return job


@pytest.fixture
def store(settings):
    return JobStore(settings.db_path)


def test_rates_are_per_view():
    r = trials.rates({"views": 2000, "shares": 6, "saved": 4, "likes": 100, "comments": 2})
    assert r["shares_per_1k"] == 3 and r["saves_per_1k"] == 2 and r["engage_per_1k"] == 5
    assert r["likes_per_100"] == 5
    assert trials.rates({"views": 0}) == {}


def test_verdicts_against_your_usual_trial(store):
    for i, views in enumerate((4000, 5000, 6000, 5000)):
        add_trial(store, f"old{i}", 48 + i, views=views, shares=5, saved=5)   # usual: 5K views, 2/1K
    add_trial(store, "big", 30, views=12500, shares=30, saved=20)         # 2.5x views
    add_trial(store, "loved", 30, views=4500, shares=20, saved=20)        # normal views, 4x engagement
    add_trial(store, "flop", 30, views=1500, shares=1, saved=0)
    add_trial(store, "fresh", 5, views=9000, shares=10, saved=10)         # first day only
    add_trial(store, "silent", 30)                                        # no numbers yet
    later = PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], "Later", idem_key="later",
                    post_id="p_later", run_at=iso(NOW + timedelta(hours=5)))
    store.insert(later, "pending")

    results = {r.key: r for r in trials.collect(store, now=NOW)}
    assert results["big"].verdict == "winner" and round(results["big"].views_x, 1) == 2.6  # vs the others: 4,750
    assert results["loved"].verdict == "winner" and results["loved"].engage_x > 3
    assert results["flop"].verdict == "below"
    assert results["fresh"].verdict == "early"
    assert results["silent"].verdict == "no_numbers"
    assert results["later"].verdict == "waiting"
    assert results["old1"].verdict == "average"
    base = trials.baseline(list(results.values()))
    assert base["trials"] == 7 and base["views"] == 5000
    assert "2.6x your usual" in trials.describe(results["big"], base)


def test_no_verdict_without_history(store):
    add_trial(store, "a", 30, views=1000)
    add_trial(store, "b", 30, views=900)
    assert {r.verdict for r in trials.collect(store, now=NOW)} == {"no_history"}


def test_paired_trial_shows_its_main(store):
    main = PostJob("instagram", "reel", "zernio", [], "Main", idem_key="main1", post_id="p_pair",
                   run_at=iso(NOW - timedelta(hours=40)))
    store.insert(main, "running")
    store.finish("main1", PostResult("instagram", "reel", "published"))
    Tracker(store).add_stats("main1", {"views": 8000})
    add_trial(store, "t1", 38, views=3000, depends_on="main1")
    r = trials.collect(store, now=NOW)[0]
    assert not r.standalone and r.main_metrics["views"] == 8000


def test_winner_lines_offer_both_ways_to_promote(store):
    for i in range(3):
        add_trial(store, f"o{i}", 50, views=5000, shares=5, saved=5)
    add_trial(store, "w", 30, views=11000, shares=30, saved=10)
    results = trials.collect(store, now=NOW)
    w = next(r for r in results if r.key == "w")
    lines = trials.verdict_lines(w, trials.baseline(results, exclude="w"), "https://vauto.example")
    assert any("Trials → Share" in line and w.url in line for line in lines)
    assert any(line.endswith("/?reuse=p_w") for line in lines)


def test_standalone_report_judges_against_usual_and_saves_numbers(settings, store, monkeypatch):
    for i in range(3):
        add_trial(store, f"o{i}", 100, views=5000, shares=5, saved=5)
    job = PostJob("instagram", "trial_reel", "zernio", [], "Node vs Deno explained\n#js", idem_key="t1",
                  post_id="p_t1", run_at=iso(utcnow() - timedelta(hours=72)))
    store.insert(job, "running")
    store.finish("t1", PostResult("instagram", "trial_reel", "published", url="https://instagram.com/reel/t1",
                                  platform_post_id="m1"))
    sent = []
    monkeypatch.setattr(insights, "metrics_for", lambda s, b, r, sess: {"views": 15000, "shares": 60, "saved": 30})
    monkeypatch.setattr(insights.notify, "send", lambda s, text, session=None: sent.append(text) or True)
    settings.env["VAUTO_PUBLIC_URL"] = "https://vauto.example"
    report = PostJob("instagram", "trial_report", "insights", [], "", options={"trial_key": "t1"})
    result = insights.InsightsPublisher(settings, session=FakeSession({})).publish(report)
    assert result.notes[0].startswith("🏆 15K views, 3.0x your usual")
    assert "Node vs Deno explained" in sent[0] and "/?reuse=p_t1" in sent[0]
    assert Tracker(store).latest_stats(["t1"])["t1"]["metrics"]["views"] == 15000  # saved for next time


# ── briefing and reminders ───────────────────────────────────────────────────

def test_briefing_due_once_a_day(settings):
    morning = datetime(2026, 10, 10, 7, 5, tzinfo=timezone.utc)  # 08:05 London
    assert briefing.briefing_due(settings, None, morning)
    assert not briefing.briefing_due(settings, "2026-10-10", morning)
    assert not briefing.briefing_due(settings, None, morning - timedelta(hours=1))  # 07:05, too early
    settings.briefing_time = "off"
    assert not briefing.briefing_due(settings, None, morning)


def test_briefing_lists_slots_and_winners(settings, store):
    settings.env["VAUTO_PUBLIC_URL"] = "https://vauto.example"
    for i in range(3):
        add_trial(store, f"o{i}", 60, views=5000, shares=5, saved=5, caption=f"Old {i}")
    add_trial(store, "w", 30, views=12000, shares=40, saved=20, caption="JavaScript runtimes explained")
    queued = PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], "c", idem_key="q",
                     post_id="p_q", run_at=iso(datetime(2026, 10, 10, 11, 30, tzinfo=timezone.utc)))  # 12:30
    store.insert(queued, "pending")
    text = briefing.briefing_text(settings, store, NOW)
    assert text.startswith("☀️ Sat 10 Oct")
    assert "12:30 Trial  Trial Reel · pending ✓" in text
    assert "18:00 Main   free: share your video with Today's main" in text
    assert "🏆 “JavaScript runtimes explained”" in text and "/?reuse=p_w" in text
    assert "Your usual trial: 5K views" in text


def test_main_reminder_once_when_slot_is_empty(settings, store, monkeypatch):
    from videoautomation import notify

    sent = []
    monkeypatch.setattr(notify, "enabled", lambda s: True)
    monkeypatch.setattr(notify, "send", lambda s, text, session=None: sent.append(text) or True)
    settings.briefing_time = "off"
    afternoon = datetime(2026, 10, 10, 14, 30, tzinfo=timezone.utc)  # 15:30 London, main at 18:00
    tracker = Tracker(store)
    assert briefing.run(settings, tracker, afternoon) == ["reminded about the 2026-10-10T17:00:00Z main slot"]
    assert "18:00 main slot is still empty" in sent[0]
    assert briefing.run(settings, tracker, afternoon + timedelta(minutes=30)) == []  # only once
    # Too early, or already filled: no reminder.
    assert briefing.main_reminders(settings, set(), datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)) == []
    main = PostJob("instagram", "reel", "zernio", [], "Main", idem_key="m", post_id="p_m",
                   run_at=iso(datetime(2026, 10, 10, 17, 0, tzinfo=timezone.utc)))
    store.insert(main, "pending")
    assert briefing.main_reminders(settings, set(), afternoon) == []


def test_housekeeping_sends_the_briefing(settings, store, monkeypatch):
    from videoautomation import notify
    from videoautomation.tracker import housekeeping

    sent = []
    monkeypatch.setattr(notify, "enabled", lambda s: True)
    monkeypatch.setattr(notify, "send", lambda s, text, session=None: sent.append(text) or True)
    settings.digest = "off"
    settings.stats_refresh_hours = 0
    settings.main_reminder_hours = 0
    done = housekeeping(settings, Tracker(store), now=NOW)
    assert "briefing sent" in done and sent[0].startswith("☀️")
    assert "briefing sent" not in housekeeping(settings, Tracker(store), now=NOW + timedelta(hours=2))


# ── daily cap ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_trial_cap_refuses_the_sixth_of_the_day(media_dir, settings, store):
    from videoautomation.pipeline import PostRequest, build_plan, execute

    at = utcnow().replace(hour=20, minute=0, second=0, microsecond=0) + timedelta(days=1)
    for i in range(5):
        job = PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], f"t{i}",
                      idem_key=f"cap{i}", post_id=f"pc{i}", run_at=iso(at - timedelta(hours=i + 1)))
        store.insert(job, "pending")
    req = PostRequest(inputs=[media_dir / "landscape.mp4"], caption="One more", platforms=["instagram"],
                      trial_only=True, publish_at=iso(at), dry_run=True)
    results = execute(build_plan(req, settings), settings, req)
    assert [(r.surface, r.status) for r in results] == [("trial_reel", "failed")]
    assert "already has 5 Trial Reels" in results[0].error

    settings.trial_daily_max = 6
    req2 = PostRequest(inputs=[media_dir / "landscape.mp4"], caption="One more", platforms=["instagram"],
                       trial_only=True, publish_at=iso(at), dry_run=True)
    assert {r.status for r in execute(build_plan(req2, settings), settings, req2)} == {"dry_run"}


# ── web ──────────────────────────────────────────────────────────────────────

def test_trial_results_endpoint(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    store = JobStore(settings.db_path)
    for i in range(4):  # each trial is compared with the other three
        add_trial(store, f"o{i}", 100, views=5000, shares=5, saved=5)
    settings.dotenv_path = tmp_path / ".env"
    client = create_app(settings).test_client()
    data = client.get("/api/trial-results").get_json()
    assert len(data["trials"]) == 4 and data["baseline"]["views"] == 5000
    assert data["trials"][0]["verdict"] == "average" and "views" in data["trials"][0]["summary"]


def test_cli_trials_uses_saved_numbers(settings, store, capsys, monkeypatch):
    from videoautomation import cli, insights

    monkeypatch.setattr(insights, "metrics_for", lambda *a: pytest.fail("must not call Instagram"))
    for i in range(3):
        add_trial(store, f"o{i}", 50, views=5000, shares=5, saved=5, caption=f"Old {i}")
    add_trial(store, "w", 30, views=12000, shares=30, saved=10, caption="Big winner")
    args = cli.build_parser().parse_args(["trials"])
    assert cli.cmd_trials(args, settings) == 0
    out = capsys.readouterr().out
    assert "Your usual trial" in out and "winner      Big winner" in out
