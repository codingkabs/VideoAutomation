from datetime import timedelta

import pytest

from videoautomation import pipeline
from videoautomation.media.variants import VariantOptions
from pathlib import Path

from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.pipeline import PostRequest, build_plan, execute
from videoautomation.scheduler import JobStore, parse_iso, run_due, utcnow

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg
CAPTION = "Hook line here\nMore text\n#a #b #c #d #e #f"


class RecordingPublisher:
    remote_schedule = False

    def __init__(self, backend):
        self.backend = backend
        self.jobs = []

    def publish(self, job):
        self.jobs.append(job)
        return PostResult(job.platform, job.surface, "published", url=f"https://{job.platform}.example/{job.surface}")

    def schedules_remotely(self, job):
        return self.remote_schedule


@pytest.fixture
def fake_publishers(monkeypatch):
    made = {}

    def factory(backend, platform, settings, storage, dry_run=False):
        pub = made.setdefault((backend, platform), RecordingPublisher(backend))
        pub.remote_schedule = backend == "zernio"
        return pub

    monkeypatch.setattr(pipeline, "make_publisher", factory)
    return made


def request(media, **kw):
    base = dict(inputs=media, caption=CAPTION, platforms=["instagram", "facebook", "tiktok", "youtube", "snapchat"])
    base.update(kw)
    return PostRequest(**base)


def test_video_plan_builds_jobs_and_trial(media_dir, settings):
    req = request([media_dir / "landscape.mp4"], trial=True, trial_delay=(60, 120),
                  variant=VariantOptions(zoom=1.2))
    plan = build_plan(req, settings)
    surfaces = [(j.platform, j.surface) for j in plan.jobs]
    assert surfaces == [("instagram", "reel"), ("facebook", "reel"), ("tiktok", "video"),
                        ("youtube", "short"), ("snapchat", "spotlight"), ("instagram", "trial_reel"),
                        ("instagram", "trial_report")]
    main, trial, report = plan.jobs[0], plan.jobs[-2], plan.jobs[-1]
    assert trial.depends_on == main.idem_key
    assert report.depends_on == trial.idem_key and report.backend == "insights"
    assert parse_iso(report.run_at) - parse_iso(trial.run_at) == timedelta(hours=72)
    assert trial.options["trial_graduation"] == "MANUAL"
    assert trial.media[0].path != main.media[0].path
    delay = parse_iso(trial.run_at) - utcnow()
    assert timedelta(minutes=59) <= delay <= timedelta(minutes=121)
    # Instagram caption is capped to 5 hashtags, others keep all 6
    assert "#f" not in main.caption and "#f" in plan.jobs[1].caption
    assert plan.jobs[3].options["title"].endswith("#Shorts")


def test_platform_rejections_are_reported(media_dir, settings):
    # the 4 s clip is below Snapchat's 5 s minimum
    plan = build_plan(request([media_dir / "vertical_silent.mp4"]), settings)
    assert [r.platform for r in plan.results] == ["snapchat"]
    assert "at least 5" in plan.results[0].error
    assert "snapchat" not in [j.platform for j in plan.jobs]


def test_trial_backend_auto_prefers_zernio(settings):
    assert settings.backends["instagram"] == "meta"
    assert settings.trial_backend_for() == "zernio"  # Instagram account connected in Zernio
    settings.zernio_accounts.pop("instagram")
    assert settings.trial_backend_for() == "meta"


def test_execute_posts_now_and_queues_trial_locally(media_dir, settings, fake_publishers):
    settings.trial_backend = "meta"
    req = request([media_dir / "landscape.mp4"], platforms=["instagram", "tiktok"], trial=True)
    plan = build_plan(req, settings)
    results = execute(plan, settings, req)
    statuses = {(r.platform, r.surface): r.status for r in results}
    assert statuses == {("instagram", "reel"): "published", ("tiktok", "video"): "published",
                        ("instagram", "trial_reel"): "queued", ("instagram", "trial_report"): "queued"}

    # nothing runs before the scheduled time; the worker posts it afterwards
    store = JobStore(settings.db_path)
    assert run_due(store, lambda j: fake_publishers[(j.backend, j.platform)]) == []
    later = run_due(store, lambda j: fake_publishers[(j.backend, j.platform)], now=utcnow() + timedelta(hours=3))
    assert [(r.surface, r.status) for r in later] == [("trial_reel", "published")]


def test_execute_schedules_trial_remotely_on_zernio(media_dir, settings, fake_publishers):
    settings.backends["instagram"] = "zernio"
    req = request([media_dir / "landscape.mp4"], platforms=["instagram"], trial=True)
    results = execute(build_plan(req, settings), settings, req)
    assert [r.status for r in results] == ["published", "published", "queued"]
    trial_job = fake_publishers[("zernio", "instagram")].jobs[-1]
    assert trial_job.surface == "trial_reel" and trial_job.run_at


def test_repeat_post_is_detected(media_dir, settings, fake_publishers):
    req = request([media_dir / "landscape.mp4"], platforms=["facebook"])
    execute(build_plan(req, settings), settings, req)
    again = execute(build_plan(req, settings), settings, req)
    assert again[0].status == "duplicate" and again[0].url == "https://facebook.example/reel"
    forced = request([media_dir / "landscape.mp4"], platforms=["facebook"], force=True)
    assert execute(build_plan(forced, settings), settings, forced)[0].status == "published"


def test_photo_post_plan(media_dir, settings):
    req = request([media_dir / "a.png", media_dir / "b.jpg"], trial=True)
    plan = build_plan(req, settings)
    by_platform = {j.platform: j for j in plan.jobs}
    assert by_platform["instagram"].surface == "carousel"
    assert all(m.kind == "image" for m in by_platform["instagram"].media)
    assert by_platform["tiktok"].options["auto_add_music"] is True
    assert by_platform["youtube"].media[0].kind == "video"
    assert by_platform["snapchat"].media[0].kind == "video"
    assert any("trial reels need a video" in n for n in plan.notes)


def test_dry_run_touches_no_database(media_dir, settings):
    req = request([media_dir / "landscape.mp4"], platforms=["instagram", "youtube"], trial=True, dry_run=True)
    results = execute(build_plan(req, settings), settings, req)
    assert {r.status for r in results} == {"dry_run"}
    assert not settings.db_path.exists()


def test_scheduled_post_queues_meta_and_schedules_zernio(media_dir, settings, fake_publishers):
    from videoautomation.scheduler import iso

    settings.trial_backend = "meta"
    at = iso(utcnow() + timedelta(hours=3))
    req = request([media_dir / "landscape.mp4"], platforms=["instagram", "tiktok"], trial=True, publish_at=at)
    plan = build_plan(req, settings)
    assert all(j.run_at for j in plan.jobs)
    trial = next(j for j in plan.jobs if j.surface == "trial_reel")
    assert parse_iso(trial.run_at) - parse_iso(at) >= timedelta(minutes=60)
    results = {(r.platform, r.surface): r.status for r in execute(plan, settings, req)}
    # Instagram waits locally, TikTok (Zernio) is scheduled remotely right away,
    # the trial waits for its pending parent.
    assert results[("instagram", "reel")] == "queued"
    assert results[("tiktok", "video")] == "published"  # fake Zernio publisher accepted it
    assert results[("instagram", "trial_reel")] == "queued"
    assert fake_publishers[("zernio", "tiktok")].jobs[0].run_at == at


def test_photos_respect_platform_size_caps(media_dir, settings):
    settings.backends["bluesky"] = "bluesky"
    req = request([media_dir / "a.png", media_dir / "b.jpg"], platforms=["bluesky", "lemon8", "pinterest"])
    plan = build_plan(req, settings)
    jobs = {j.platform: j for j in plan.jobs}
    assert jobs["bluesky"].surface == "images"
    assert all(Path(m.path).stat().st_size <= 0.95 * 1024 * 1024 for m in jobs["bluesky"].media)
    assert jobs["lemon8"].backend == "handoff" and len(jobs["lemon8"].media) == 2
    assert len(jobs["pinterest"].media) == 1  # one image per pin
    assert any("only the first 1 of 2" in n for n in plan.notes)


def test_subtitles_from_file_go_on_every_version(media_dir, settings, tmp_path):
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:02,000\nHello there\n")
    req = request([media_dir / "landscape.mp4"], platforms=["instagram", "tiktok"], trial=True, subtitles=str(srt))
    plan = build_plan(req, settings)
    main = next(j for j in plan.jobs if j.surface == "reel")
    trial = next(j for j in plan.jobs if j.surface == "trial_reel")
    assert "subs" in Path(main.media[0].path).name
    assert Path(trial.media[0].path).with_suffix(".ass").is_file()


def test_retry_runs_a_failed_job(media_dir, settings, monkeypatch):
    from videoautomation import service

    store = JobStore(settings.db_path)
    job = PostJob("facebook", "reel", "meta", [MediaFile(str(media_dir / "landscape.mp4"), "video")], "c",
                  idem_key="failedjob1", post_id="p")
    store.insert(job, "running")
    store.finish("failedjob1", PostResult("facebook", "reel", "failed", error="boom"))
    monkeypatch.setattr(service, "publisher_factory", lambda s: (lambda j: RecordingPublisher("meta")))
    result = service.retry(settings, "failedjob")
    assert result.status == "published" and store.get("failedjob1").status == "published"
