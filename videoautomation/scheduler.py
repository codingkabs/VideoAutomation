"""SQLite job store, retry logic, and the worker that runs delayed posts.

Jobs routed through Zernio are scheduled on Zernio's side (``scheduledFor``),
so they never wait here. Jobs for direct backends (Meta) with a future
``run_at`` sit in this queue until ``vauto worker`` picks them up.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

from .errors import ConfigError, PublishError, VautoError
from .models import PostJob, PostResult

RETRY_DELAYS = (5.0, 20.0)  # seconds between inline attempts on transient errors

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    idem_key    TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    platform    TEXT NOT NULL,
    surface     TEXT NOT NULL,
    backend     TEXT NOT NULL,
    payload     TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    status      TEXT NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    not_before  TEXT,
    result      TEXT,
    depends_on  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_due ON jobs (status, run_at);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class JobRow:
    def __init__(self, row: sqlite3.Row):
        self.idem_key: str = row["idem_key"]
        self.status: str = row["status"]
        self.attempts: int = row["attempts"]
        self.run_at: str = row["run_at"]
        self.job = PostJob.from_dict(json.loads(row["payload"]))
        self.result = PostResult(**json.loads(row["result"])) if row["result"] else None


class _LockedConnection:
    """Serialize access so publishing threads can share one connection."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, args)

    def executescript(self, sql: str) -> None:
        with self._lock:
            self._conn.executescript(sql)


class JobStore:
    """Job table in SQLite. Pass ``Path(":memory:")`` for a throwaway store."""

    def __init__(self, path: Path):
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(path), isolation_level=None, timeout=30, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self.conn = _LockedConnection(raw)
        self.conn.executescript(SCHEMA)

    # ---------------------------------------------------------------- queries
    def get(self, idem_key: str) -> JobRow | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE idem_key = ?", (idem_key,)).fetchone()
        return JobRow(row) if row else None

    def rows(self, include_done: bool = True, post_id: str | None = None, limit: int = 50) -> list[JobRow]:
        sql = "SELECT * FROM jobs"
        where, args = [], []
        if not include_done:
            where.append("status IN ('pending', 'running')")
        if post_id:
            where.append("post_id = ?")
            args.append(post_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, run_at DESC LIMIT ?"
        args.append(limit)
        return [JobRow(r) for r in self.conn.execute(sql, args).fetchall()]

    # ---------------------------------------------------------------- writes
    def insert(self, job: PostJob, status: str = "pending") -> bool:
        """Insert a new job. Returns False if the idempotency key already exists."""
        now = iso(utcnow())
        try:
            self.conn.execute(
                "INSERT INTO jobs (idem_key, post_id, platform, surface, backend, payload, run_at, status,"
                " depends_on, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job.idem_key, job.post_id, job.platform, job.surface, job.backend,
                 json.dumps(job.to_dict()), job.run_at or now, status, job.depends_on, now, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    def replace_pending(self, job: PostJob) -> None:
        """Forget an earlier failed/skipped attempt so a re-run can insert fresh."""
        self.conn.execute(
            "DELETE FROM jobs WHERE idem_key = ? AND status IN ('failed', 'skipped', 'dry_run')", (job.idem_key,)
        )

    def finish(self, idem_key: str, result: PostResult) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, result = ?, updated_at = ? WHERE idem_key = ?",
            (result.status, json.dumps(result.to_dict()), iso(utcnow()), idem_key),
        )

    def bump_attempts(self, idem_key: str) -> None:
        self.conn.execute("UPDATE jobs SET attempts = attempts + 1 WHERE idem_key = ?", (idem_key,))

    def defer(self, idem_key: str, seconds: float) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = 'pending', not_before = ?, updated_at = ? WHERE idem_key = ?",
            (iso(utcnow() + timedelta(seconds=seconds)), iso(utcnow()), idem_key),
        )

    def claim_due(self, now: datetime | None = None) -> list[JobRow]:
        """Atomically move due pending jobs to running and return them."""
        stamp = iso(now or utcnow())
        claimed = []
        rows = self.conn.execute(
            "SELECT idem_key FROM jobs WHERE status = 'pending' AND run_at <= ?"
            " AND (not_before IS NULL OR not_before <= ?) ORDER BY run_at",
            (stamp, stamp),
        ).fetchall()
        for row in rows:
            cur = self.conn.execute(
                "UPDATE jobs SET status = 'running', updated_at = ? WHERE idem_key = ? AND status = 'pending'",
                (stamp, row["idem_key"]),
            )
            if cur.rowcount == 1:
                claimed.append(self.get(row["idem_key"]))
        return [c for c in claimed if c is not None]

    def release_stale(self, older_than_minutes: int = 30) -> int:
        """Return jobs stuck in 'running' (for example after a crash) to the queue."""
        cutoff = iso(utcnow() - timedelta(minutes=older_than_minutes))
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'pending' WHERE status = 'running' AND updated_at < ?", (cutoff,)
        )
        return cur.rowcount


# ------------------------------------------------------------------- execution

PublisherFor = Callable[[PostJob], "object"]


def run_job(
    store: JobStore,
    job: PostJob,
    publisher_for: PublisherFor,
    sleep: Callable[[float], None] = time.sleep,
) -> PostResult:
    """Publish one stored job with inline retries on transient errors."""
    if job.depends_on:
        parent = store.get(job.depends_on)
        if parent is None or parent.status in ("failed", "skipped"):
            result = PostResult(job.platform, job.surface, "skipped",
                                error="skipped because the main post did not publish")
            store.finish(job.idem_key, result)
            return result
        if parent.status in ("pending", "running"):
            store.defer(job.idem_key, 300)
            return PostResult(job.platform, job.surface, "queued", run_at=job.run_at,
                              notes=["waiting for the main post to publish"])

    attempt = 0
    while True:
        attempt += 1
        store.bump_attempts(job.idem_key)
        try:
            publisher = publisher_for(job)
            result = publisher.publish(job)  # type: ignore[attr-defined]
        except PublishError as exc:
            if exc.transient and attempt <= len(RETRY_DELAYS):
                sleep(RETRY_DELAYS[attempt - 1])
                continue
            result = PostResult(job.platform, job.surface, "failed", error=str(exc))
        except (ConfigError, VautoError) as exc:
            result = PostResult(job.platform, job.surface, "failed", error=str(exc))
        except Exception as exc:  # never let one platform crash the batch
            result = PostResult(job.platform, job.surface, "failed", error=f"{type(exc).__name__}: {exc}")
        store.finish(job.idem_key, result)
        return result


def run_due(store: JobStore, publisher_for: PublisherFor, now: datetime | None = None) -> list[PostResult]:
    store.release_stale()
    return [run_job(store, row.job, publisher_for) for row in store.claim_due(now)]


def worker_loop(
    store: JobStore,
    publisher_for: PublisherFor,
    interval: float = 30.0,
    once: bool = False,
) -> Iterator[PostResult]:
    """Yield results as due jobs run. With ``once`` it drains what is due and stops."""
    while True:
        yield from run_due(store, publisher_for)
        if once:
            return
        time.sleep(interval)
