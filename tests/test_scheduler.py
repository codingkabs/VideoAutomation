from datetime import timedelta
from pathlib import Path

from videoautomation.errors import PublishError
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.scheduler import JobStore, iso, run_due, run_job, utcnow


def make_job(key="k1", run_at=None, depends_on=None):
    return PostJob("instagram", "reel", "meta", [MediaFile("/tmp/x.mp4", "video")], "cap",
                   idem_key=key, post_id="p", run_at=run_at, depends_on=depends_on)


class Pub:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def publish(self, job):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def ok(job_platform="instagram"):
    return PostResult(job_platform, "reel", "published", url="https://example/p")


def test_insert_is_idempotent():
    store = JobStore(Path(":memory:"))
    assert store.insert(make_job())
    assert not store.insert(make_job())


def test_transient_error_retries_then_succeeds():
    store = JobStore(Path(":memory:"))
    job = make_job()
    store.insert(job, "running")
    pub = Pub([PublishError("503", transient=True), ok()])
    sleeps = []
    result = run_job(store, job, lambda j: pub, sleep=sleeps.append)
    assert result.status == "published" and pub.calls == 2 and len(sleeps) == 1
    assert store.get("k1").status == "published"


def test_permanent_error_fails_without_retry():
    store = JobStore(Path(":memory:"))
    job = make_job()
    store.insert(job, "running")
    pub = Pub([PublishError("bad caption")])
    result = run_job(store, job, lambda j: pub, sleep=lambda s: None)
    assert result.status == "failed" and "bad caption" in result.error and pub.calls == 1


def test_unexpected_exception_does_not_crash():
    store = JobStore(Path(":memory:"))
    job = make_job()
    store.insert(job, "running")
    result = run_job(store, job, lambda j: Pub([ValueError("oops")]), sleep=lambda s: None)
    assert result.status == "failed" and "ValueError" in result.error


def test_claim_due_only_returns_due_jobs():
    store = JobStore(Path(":memory:"))
    store.insert(make_job("now", run_at=iso(utcnow() - timedelta(minutes=1))))
    store.insert(make_job("later", run_at=iso(utcnow() + timedelta(hours=1))))
    assert [r.idem_key for r in store.claim_due()] == ["now"]
    assert store.claim_due() == []  # already claimed
    later = store.claim_due(utcnow() + timedelta(hours=2))
    assert [r.idem_key for r in later] == ["later"]


def test_dependent_job_skipped_when_parent_failed():
    store = JobStore(Path(":memory:"))
    parent = make_job("parent")
    store.insert(parent, "running")
    store.finish("parent", PostResult("instagram", "reel", "failed", error="x"))
    child = make_job("child", run_at=iso(utcnow() - timedelta(seconds=1)), depends_on="parent")
    store.insert(child)
    results = run_due(store, lambda j: Pub([ok()]))
    assert results[0].status == "skipped"


def test_dependent_job_waits_for_pending_parent():
    store = JobStore(Path(":memory:"))
    store.insert(make_job("parent", run_at=iso(utcnow() + timedelta(hours=1))))
    child = make_job("child", run_at=iso(utcnow() - timedelta(seconds=1)), depends_on="parent")
    store.insert(child)
    results = run_due(store, lambda j: Pub([ok()]))
    assert results[0].status == "queued"
    assert store.get("child").status == "pending"
