"""Your post history: every video or photo set you posted, where it went, and how it did.

Lives in the same SQLite file as the job queue. A post is one piece of media
(``post_id``); its jobs are the per-platform posts. Numbers are saved as
snapshots so you can see them grow.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .config import Settings, load_platforms, platform_spec
from .errors import VautoError
from .ffmpeg import run_ffmpeg
from .models import PostResult, label_for
from .scheduler import JobRow, JobStore, iso, parse_iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id     TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    caption     TEXT NOT NULL,
    platforms   TEXT NOT NULL,
    inputs      TEXT NOT NULL DEFAULT '[]',
    rejected    TEXT NOT NULL DEFAULT '[]',
    thumb       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stats (
    idem_key    TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (idem_key, fetched_at)
);
CREATE TABLE IF NOT EXISTS snippets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    text        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
"""

METRICS = ("views", "reach", "likes", "comments", "shares", "saved")
LIVE = {"published"}
UPCOMING = {"pending", "running", "scheduled", "queued"}
HASHTAG = re.compile(r"(?<![\w#])#(\w+)", re.UNICODE)


def _spec_name(platform: str) -> str:
    try:
        return platform_spec(platform)["name"]
    except VautoError:
        return platform


def short(text: str, n: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def compact(n: float) -> str:
    """12345 -> 12.3k"""
    n = float(n or 0)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(n) >= size:
            value = f"{n / size:.1f}".rstrip("0").rstrip(".")
            return f"{value}{suffix}"
    return f"{n:.0f}"


def make_thumb(source: Path, dest: Path, is_video: bool) -> Path | None:
    """A small JPEG for the post list. Best effort."""
    if dest.is_file():
        return dest
    for offset in (("-ss", "1") if is_video else (), ()):
        try:
            run_ffmpeg([*offset, "-i", str(source), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", str(dest)])
        except VautoError:
            continue
        if dest.is_file() and dest.stat().st_size:
            return dest
    return None


class Tracker:
    def __init__(self, store: JobStore | Path):
        self.store = store if isinstance(store, JobStore) else JobStore(store)
        self.conn = self.store.conn
        self.conn.executescript(SCHEMA)

    @classmethod
    def open(cls, settings: Settings) -> "Tracker":
        return cls(JobStore(settings.db_path))

    # ------------------------------------------------------------ key/value
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                          (key, value, value))

    # ------------------------------------------------------------ recording
    def record_post(self, plan, req) -> None:
        """Called once per real (not dry-run) post, before anything publishes."""
        now = iso(utcnow())
        thumb = None
        media = next((m for j in plan.jobs for m in j.media), None)
        if media is not None:
            made = make_thumb(Path(media.path), Path(plan.work_dir) / "thumb.jpg", media.kind == "video")
            thumb = str(made) if made else None
        rejected = [{"platform": r.platform, "surface": r.surface, "error": r.error} for r in plan.results
                    if r.status == "failed"]
        existing = self.conn.execute("SELECT * FROM posts WHERE post_id = ?", (plan.post_id,)).fetchone()
        platforms = list(req.platforms)
        if existing:
            platforms = list(dict.fromkeys(json.loads(existing["platforms"]) + platforms))
            self.conn.execute(
                "UPDATE posts SET caption = ?, platforms = ?, inputs = ?, rejected = ?, thumb = COALESCE(?, thumb),"
                " updated_at = ? WHERE post_id = ?",
                (req.caption, json.dumps(platforms), json.dumps([str(p) for p in req.inputs]),
                 json.dumps(rejected), thumb, now, plan.post_id))
        else:
            self.conn.execute(
                "INSERT INTO posts (post_id, kind, caption, platforms, inputs, rejected, thumb, created_at,"
                " updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (plan.post_id, plan.kind, req.caption, json.dumps(platforms),
                 json.dumps([str(p) for p in req.inputs]), json.dumps(rejected), thumb, now, now))

    def post_row(self, post_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

    def forget(self, post_id: str) -> None:
        """Remove a post from your history (it stays on the platforms)."""
        rows = self.store.rows(post_id=post_id, limit=1000)
        if any(r.status in ("pending", "running") for r in rows):
            raise VautoError("This post still has queued jobs; cancel them first")
        keys = [r.idem_key for r in rows]
        for key in keys:
            self.conn.execute("DELETE FROM stats WHERE idem_key = ?", (key,))
        self.conn.execute("DELETE FROM jobs WHERE post_id = ?", (post_id,))
        self.conn.execute("DELETE FROM posts WHERE post_id = ?", (post_id,))

    # ---------------------------------------------------------------- stats
    def add_stats(self, idem_key: str, metrics: dict[str, float], source: str = "",
                  at: datetime | None = None) -> None:
        clean = {k: float(v) for k, v in metrics.items() if k in METRICS and v is not None}
        self.conn.execute(
            "INSERT OR REPLACE INTO stats (idem_key, fetched_at, metrics, source) VALUES (?,?,?,?)",
            (idem_key, iso(at or utcnow()), json.dumps(clean), source))

    def latest_stats(self, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        keys = list(keys)
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                f"SELECT s.* FROM stats s JOIN (SELECT idem_key, MAX(fetched_at) AS f FROM stats"
                f" WHERE idem_key IN ({marks}) GROUP BY idem_key) m"
                f" ON s.idem_key = m.idem_key AND s.fetched_at = m.f", chunk).fetchall()
            for r in rows:
                out[r["idem_key"]] = {"metrics": json.loads(r["metrics"]), "at": r["fetched_at"], "source": r["source"]}
        return out

    def history(self, idem_key: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM stats WHERE idem_key = ? ORDER BY fetched_at", (idem_key,)).fetchall()
        return [{"at": r["fetched_at"], "metrics": json.loads(r["metrics"]), "source": r["source"]} for r in rows]

    # ----------------------------------------------------------- job fixes
    def find(self, key_prefix: str) -> JobRow:
        rows = self.conn.execute("SELECT idem_key FROM jobs WHERE idem_key LIKE ?",
                                 (key_prefix.replace("%", "") + "%",)).fetchall()
        if not rows:
            raise VautoError(f"No post starting with {key_prefix!r}")
        if len(rows) > 1:
            raise VautoError(f"{key_prefix!r} matches several posts; use more characters")
        row = self.store.get(rows[0]["idem_key"])
        assert row is not None
        return row

    def mark_posted(self, key_prefix: str, url: str | None = None) -> PostResult:
        """For hand-off and browser posts you finished yourself: record that it is live."""
        row = self.find(key_prefix)
        if row.status in ("pending", "running"):
            raise VautoError("That post is still queued; cancel it first or wait for it")
        url = (url or "").strip() or None
        if url and not re.match(r"^https?://", url):
            raise VautoError("The link should start with https://")
        prior = row.result
        result = PostResult(row.job.platform, row.job.surface, "published", url=url or (prior.url if prior else None),
                            remote_id=prior.remote_id if prior else None, run_at=row.run_at,
                            platform_post_id=prior.platform_post_id if prior else None,
                            notes=["marked as posted by you"])
        self.store.finish(row.idem_key, result)
        return result

    def set_numbers(self, key_prefix: str, values: dict[str, Any]) -> dict[str, float]:
        """Numbers you typed in yourself (for routes that cannot report them)."""
        row = self.find(key_prefix)
        metrics: dict[str, float] = {}
        for key in METRICS:
            value = values.get(key)
            if value in (None, ""):
                continue
            try:
                number = float(str(value).replace(",", "").strip())
            except ValueError as exc:
                raise VautoError(f"{key} should be a number") from exc
            if number < 0:
                raise VautoError(f"{key} cannot be negative")
            metrics[key] = number
        if not metrics:
            raise VautoError("Type at least one number")
        if row.status in ("handoff", "draft", "submitted"):
            self.mark_posted(row.idem_key)  # it has views, so it is live
        self.add_stats(row.idem_key, metrics, source="you")
        return metrics

    # ------------------------------------------------------------- listing
    def _grouped(self, limit_posts: int = 1000) -> list[tuple[str, list[JobRow]]]:
        # Newest first by when the post goes out (scheduled posts sit on top).
        order = self.conn.execute(
            "SELECT post_id, MAX(at) AS latest FROM ("
            " SELECT post_id, run_at AS at FROM jobs WHERE surface NOT IN ('trial_reel', 'trial_report')"
            " UNION ALL SELECT post_id, created_at FROM posts)"
            " GROUP BY post_id ORDER BY latest DESC LIMIT ?", (limit_posts,)).fetchall()
        ids = [r["post_id"] for r in order]
        by_post: dict[str, list[JobRow]] = defaultdict(list)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for raw in self.conn.execute(f"SELECT * FROM jobs WHERE post_id IN ({marks}) ORDER BY run_at", chunk):
                by_post[raw["post_id"]].append(JobRow(raw))
        return [(pid, by_post.get(pid, [])) for pid in ids]

    def posts(self, settings: Settings | None = None, query: str = "", platform: str = "", show: str = "all",
              limit: int = 50, offset: int = 0, now: datetime | None = None) -> dict[str, Any]:
        now = now or utcnow()
        items = [self.describe(pid, rows, settings, now) for pid, rows in self._grouped()]
        query = query.strip().lower()
        if query:
            items = [p for p in items if query in p["caption"].lower() or query in p["post_id"]]
        if platform:
            items = [p for p in items if any(j["platform"] == platform for j in p["jobs"] + p["rejected"])]
        if show != "all":
            items = [p for p in items if p["state"] == show or (show == "attention" and p["needs_you"])]
        return {"total": len(items), "posts": items[offset:offset + limit]}

    def describe(self, post_id: str, rows: list[JobRow] | None = None, settings: Settings | None = None,
                 now: datetime | None = None) -> dict[str, Any]:
        from .stats import reader_for

        now = now or utcnow()
        rows = rows if rows is not None else sorted(self.store.rows(post_id=post_id, limit=1000),
                                                    key=lambda r: r.run_at)
        post = self.post_row(post_id) or {}
        rows = [r for r in rows if r.job.surface != "trial_report"]
        if not rows and not post:
            raise VautoError(f"No post {post_id!r}")
        latest = self.latest_stats(r.idem_key for r in rows)
        jobs, totals = [], Counter()
        for r in rows:
            res = r.result
            stats = latest.get(r.idem_key)
            metrics = stats["metrics"] if stats else {}
            for key, value in metrics.items():
                totals[key] += value
            status = r.status
            if status == "scheduled" and parse_iso(r.run_at) <= now:
                status = "scheduled_past"  # Zernio has probably posted it; refresh to confirm
            jobs.append({
                "id": r.idem_key[:10], "platform": r.job.platform, "name": _spec_name(r.job.platform),
                "label": label_for(r.job.platform, r.job.surface), "surface": r.job.surface,
                "backend": r.job.backend, "status": status, "run_at": r.run_at,
                "url": res.url if res else None, "error": res.error if res else None,
                "notes": res.notes if res else [], "caption": r.job.caption,
                "first_comment": r.job.options.get("first_comment"),
                "media": [{"kind": m.kind, "path": m.path} for m in r.job.media],
                "metrics": metrics, "stats_at": stats["at"] if stats else None,
                "stats_source": stats["source"] if stats else None,
                "auto_stats": bool(settings and res and status == "published"
                                   and reader_for(settings, r.job, res) is not None),
            })
        rejected = json.loads(post.get("rejected") or "[]")
        for item in rejected:
            item["name"] = _spec_name(item["platform"])
            item["label"] = label_for(item["platform"], item["surface"])
        statuses = {j["status"] for j in jobs}
        main = [j for j in jobs if j["surface"] not in ("trial_reel", "story")] or jobs
        if statuses & {"pending", "running", "scheduled"} and not statuses & LIVE:
            state = "upcoming"
        elif statuses & LIVE or "scheduled_past" in statuses:
            state = "posted"
        elif statuses & {"draft", "handoff", "submitted", "held"}:
            state = "waiting"
        elif statuses == {"skipped"} and not rejected:
            state = "cancelled"
        elif jobs or rejected:
            state = "failed"
        else:
            state = "posted"
        needs_you = bool(statuses & {"failed", "handoff", "draft", "held"} or rejected)
        caption = post.get("caption") or (main[0]["caption"] if main else "")
        posted_at = min((j["run_at"] for j in main), default=post.get("created_at"))
        thumb = post.get("thumb")
        return {
            "post_id": post_id, "kind": post.get("kind") or ("video" if any(
                m.kind == "video" for r in rows for m in r.job.media) else "photos"),
            "caption": caption, "posted_at": posted_at, "created_at": post.get("created_at") or posted_at,
            "thumb": thumb if thumb and Path(thumb).is_file() else None,
            "inputs": json.loads(post.get("inputs") or "[]"),
            "jobs": jobs, "rejected": rejected, "state": state, "needs_you": needs_you,
            "totals": {k: totals.get(k, 0.0) for k in METRICS},
            "drafts": sum(1 for j in jobs if j["status"] == "held"),
            "upcoming": sorted((j for j in jobs if j["status"] in ("pending", "scheduled")), key=lambda j: j["run_at"]),
        }

    # ------------------------------------------------------------ summaries
    def recent_count(self, platform: str, hours: int = 24, now: datetime | None = None) -> int:
        now = now or utcnow()
        lo, hi = iso(now - timedelta(hours=hours)), iso(now + timedelta(hours=hours))
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE platform = ? AND surface != 'trial_report'"
            " AND status IN ('published', 'scheduled', 'draft', 'submitted', 'pending', 'running')"
            " AND run_at >= ? AND run_at <= ?", (platform, lo, hi)).fetchone()
        return int(row["n"])

    def limit_notes(self, platforms: Iterable[str], now: datetime | None = None) -> list[str]:
        notes = []
        for p in platforms:
            limit = (load_platforms().get(p) or {}).get("daily_limit")
            if not limit:
                continue
            used = self.recent_count(p, now=now)
            name = _spec_name(p)
            if used >= limit:
                notes.append(f"{name}: {used} posts within 24 hours of this one; its daily limit is {limit}, "
                             "so it may be refused")
            elif used >= 0.8 * limit:
                notes.append(f"{name}: {used} of {limit} daily posts used")
        return notes

    def summary(self, settings: Settings, days: int | None = 30, now: datetime | None = None) -> dict[str, Any]:
        from .timing import zone

        now = now or utcnow()
        tz = zone(settings)
        since = now - timedelta(days=days) if days else None
        posts = [p for p in (self.describe(pid, rows, settings, now) for pid, rows in self._grouped(5000))]
        in_window = [p for p in posts if p["posted_at"] and (since is None or parse_iso(p["posted_at"]) >= since)
                     and parse_iso(p["posted_at"]) <= now]

        totals = Counter()
        per_platform: dict[str, dict[str, Any]] = {}
        by_hour: dict[int, list[float]] = defaultdict(list)
        tag_views: dict[str, list[float]] = defaultdict(list)
        live_jobs = 0
        failed = 0
        for p in in_window:
            post_views = 0.0
            for j in p["jobs"]:
                if j["status"] == "failed":
                    failed += 1
                if j["status"] not in ("published", "scheduled_past"):
                    continue
                live_jobs += 1
                m = j["metrics"]
                post_views += m.get("views", 0.0)
                for key in METRICS:
                    totals[key] += m.get(key, 0.0)
                row = per_platform.setdefault(j["platform"], {
                    "platform": j["platform"], "name": j["name"], "posts": 0, "with_numbers": 0,
                    **{k: 0.0 for k in METRICS}, "best": None})
                row["posts"] += 1
                if m:
                    row["with_numbers"] += 1
                for key in METRICS:
                    row[key] += m.get(key, 0.0)
                if m.get("views") and (row["best"] is None or m["views"] > row["best"]["views"]):
                    row["best"] = {"post_id": p["post_id"], "views": m["views"], "url": j["url"],
                                   "caption": short(p["caption"], 50)}
            failed += len(p["rejected"])
            if post_views:
                local_hour = parse_iso(p["posted_at"]).astimezone(tz).hour
                by_hour[local_hour].append(post_views)
                for tag in {t.lower() for t in HASHTAG.findall(p["caption"])}:
                    tag_views[tag].append(post_views)
        for row in per_platform.values():
            row["avg_views"] = row["views"] / row["with_numbers"] if row["with_numbers"] else 0.0
        platforms = sorted(per_platform.values(), key=lambda r: (-r["views"], -r["posts"], r["name"]))

        top = sorted((p for p in in_window if p["totals"]["views"] or p["totals"]["likes"]),
                     key=lambda p: (-p["totals"]["views"], -p["totals"]["likes"]))[:5]

        # Posting calendar: posts per local day over the last 12 weeks.
        today = now.astimezone(tz).date()
        start = today - timedelta(days=7 * 12 - 1)
        per_day = Counter()
        for p in posts:
            if not p["posted_at"] or p["state"] in ("upcoming", "failed"):
                continue
            day = parse_iso(p["posted_at"]).astimezone(tz).date()
            if start <= day <= today:
                per_day[day.isoformat()] += 1
        streak = 0
        day = today if per_day.get(today.isoformat()) else today - timedelta(days=1)
        while per_day.get(day.isoformat()):
            streak += 1
            day -= timedelta(days=1)

        upcoming = sorted(({**j, "post_id": p["post_id"], "caption": short(p["caption"], 50), "thumb": p["thumb"]}
                           for p in posts for j in p["upcoming"]), key=lambda j: j["run_at"])
        tags = sorted(({"tag": f"#{t}", "posts": len(v), "avg_views": sum(v) / len(v)}
                       for t, v in tag_views.items()),
                      # A tag used on several posts says more than one lucky post.
                      key=lambda t: (t["posts"] < 2, -t["avg_views"], -t["posts"]))[:10]
        return {
            "days": days, "posts": len(in_window), "live": live_jobs, "failed": failed,
            "totals": {k: totals.get(k, 0.0) for k in METRICS},
            "platforms": platforms, "top": top,
            "by_hour": [{"hour": h, "posts": len(by_hour[h]), "avg_views": sum(by_hour[h]) / len(by_hour[h])}
                        for h in sorted(by_hour)],
            "calendar": {"start": start.isoformat(), "end": today.isoformat(), "days": dict(per_day)},
            "streak": streak, "upcoming": upcoming[:20], "hashtags": tags,
            "refreshed_at": self.get_meta("stats_refreshed_at"),
            "has_numbers": any(v for v in totals.values()),
            # Patterns (best hour, best hashtags) mean little until a few posts have numbers.
            "enough_data": sum(len(v) for v in by_hour.values()) >= 3,
        }

    # --------------------------------------------------------------- export
    def export_csv(self, settings: Settings) -> str:
        from .timing import zone

        tz = zone(settings)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["post_id", "posted_at", "platform", "type", "status", "link", *METRICS, "caption", "error"])
        for pid, rows in self._grouped(100000):
            p = self.describe(pid, rows, settings)
            for j in p["jobs"]:
                local = parse_iso(j["run_at"]).astimezone(tz).strftime("%Y-%m-%d %H:%M") if j["run_at"] else ""
                writer.writerow([pid, local, j["name"], j["surface"].replace("_", " "), j["status"], j["url"] or "",
                                 *[f"{j['metrics'].get(k, ''):.0f}" if k in j["metrics"] else "" for k in METRICS],
                                 j["caption"], j["error"] or ""])
            for r in p["rejected"]:
                writer.writerow([pid, "", r["name"], r["surface"], "not posted", "", *[""] * len(METRICS),
                                 p["caption"], r["error"] or ""])
        return buf.getvalue()

    # ------------------------------------------------------------- snippets
    def snippets(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute("SELECT id, name, text FROM snippets ORDER BY name COLLATE NOCASE")]

    def add_snippet(self, name: str, text: str) -> dict[str, Any]:
        name, text = name.strip(), text.strip()
        if not name or not text:
            raise VautoError("Give the saved text a name and some text")
        if len(name) > 60 or len(text) > 5000:
            raise VautoError("That is too long to save")
        existing = self.conn.execute("SELECT id FROM snippets WHERE name = ?", (name,)).fetchone()
        if existing:
            self.conn.execute("UPDATE snippets SET text = ? WHERE id = ?", (text, existing["id"]))
            return {"id": existing["id"], "name": name, "text": text}
        cur = self.conn.execute("INSERT INTO snippets (name, text, created_at) VALUES (?,?,?)",
                                (name, text, iso(utcnow())))
        return {"id": cur.lastrowid, "name": name, "text": text}

    def delete_snippet(self, snippet_id: int) -> None:
        self.conn.execute("DELETE FROM snippets WHERE id = ?", (int(snippet_id),))


# ---------------------------------------------------------------- digest


def digest_text(settings: Settings, tracker: Tracker, days: int = 7, now: datetime | None = None) -> str:
    s = tracker.summary(settings, days=days, now=now)
    t = s["totals"]
    lines = [f"📈 Your last {days} days on vauto",
             f"{s['posts']} post(s) · {s['live']} live across platforms"
             + (f" · {s['failed']} failed" if s["failed"] else "")]
    if s["has_numbers"]:
        lines.append(f"👁 {compact(t['views'])} views · ♥ {compact(t['likes'])} · 💬 {compact(t['comments'])}"
                     f" · ↗ {compact(t['shares'])}")
        if s["platforms"] and s["platforms"][0]["views"]:
            best = s["platforms"][0]
            lines.append(f"Top platform: {best['name']} ({compact(best['views'])} views)")
        if s["top"]:
            p = s["top"][0]
            link = next((j["url"] for j in p["jobs"] if j["url"]), "")
            lines.append(f"Best post: “{short(p['caption'], 50)}” ({compact(p['totals']['views'])} views) {link}".rstrip())
    elif s["posts"]:
        lines.append("No view counts yet: connect Instagram insights or Zernio analytics, or type numbers in My posts.")
    if s["streak"] > 1:
        lines.append(f"🔥 {s['streak']}-day posting streak")
    if s["upcoming"]:
        lines.append(f"🗓 {len(s['upcoming'])} post(s) coming up")
    return "\n".join(lines)


def digest_due(settings: Settings, tracker: Tracker, now: datetime | None = None) -> bool:
    """Weekly: Monday 09:00 local or later, once per week. Daily: 09:00 or later, once per day."""
    from .timing import zone

    mode = settings.digest
    if mode not in ("weekly", "daily"):
        return False
    local = (now or utcnow()).astimezone(zone(settings))
    if local.hour < 9:
        return False
    if mode == "weekly":
        if local.weekday() != 0:
            return False
        stamp = "%d-W%02d" % local.isocalendar()[:2]
    else:
        stamp = local.date().isoformat()
    return tracker.get_meta("digest_sent") != stamp


def mark_digest_sent(settings: Settings, tracker: Tracker, now: datetime | None = None) -> None:
    from .timing import zone

    local = (now or utcnow()).astimezone(zone(settings))
    stamp = "%d-W%02d" % local.isocalendar()[:2] if settings.digest == "weekly" else local.date().isoformat()
    tracker.set_meta("digest_sent", stamp)


KEEP_IN_RENDERS = ("thumb.jpg", "subtitles.srt")  # tiny: the post list thumbnail and your edited subtitles


def cleanup(settings: Settings, tracker: Tracker, days: int | None = None, now: datetime | None = None,
            dry_run: bool = False) -> dict[str, int]:
    """Delete rendered videos, uploads and hand-off files older than ``days``.

    Files of posts still waiting in the local queue are kept, and so are each
    post's thumbnail and subtitles, so My posts still looks right.
    """
    from datetime import timezone

    days = settings.keep_files_days if days is None else days
    freed = {"files": 0, "bytes": 0}
    if days <= 0:
        return freed
    cutoff = (now or utcnow()) - timedelta(days=days)
    queued = {r.job.post_id for r in tracker.store.rows(include_done=False, limit=5000)}

    def old(path: Path) -> bool:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff

    def remove(path: Path) -> None:
        freed["files"] += 1
        freed["bytes"] += path.stat().st_size
        if not dry_run:
            path.unlink()

    renders = settings.renders_dir
    if renders.is_dir():
        for post_dir in (d for d in renders.iterdir() if d.is_dir() and d.name not in queued):
            for f in post_dir.iterdir():
                if f.is_file() and f.name not in KEEP_IN_RENDERS and old(f):
                    remove(f)
    # Whole folders: web/phone uploads, hand-offs, and videos the Telegram bot received.
    for base, depth in ((settings.home / "uploads", 1), (settings.outbox_dir, 1), (settings.home / "inbox", 2)):
        if not base.is_dir():
            continue
        folders = [base] if depth == 0 else list(base.glob("/".join(["*"] * depth)))
        for folder in (f for f in folders if f.is_dir()):
            files = [f for f in folder.rglob("*") if f.is_file()]
            if files and all(old(f) for f in files) and folder.name not in queued:
                for f in files:
                    remove(f)
                if not dry_run:
                    for sub in sorted((d for d in folder.rglob("*") if d.is_dir()), reverse=True):
                        sub.rmdir()
                    folder.rmdir()
    return freed


def housekeeping(settings: Settings, tracker: Tracker, now: datetime | None = None, session=None) -> list[str]:
    """Run by the worker: refresh numbers every few hours and send the digest."""
    from . import notify
    from .stats import refresh

    now = now or utcnow()
    done = []
    hours = settings.stats_refresh_hours
    last = tracker.get_meta("stats_refreshed_at")
    if hours > 0 and (last is None or parse_iso(last) <= now - timedelta(hours=hours)):
        has_live = tracker.conn.execute("SELECT 1 FROM jobs WHERE status IN ('published', 'scheduled') LIMIT 1").fetchone()
        if has_live:
            done.append("stats: " + refresh(settings, tracker, session=session, now=now).text())
        else:
            tracker.set_meta("stats_refreshed_at", iso(now))
    last_clean = tracker.get_meta("cleaned_at")
    if settings.keep_files_days > 0 and (last_clean is None or parse_iso(last_clean) <= now - timedelta(days=1)):
        freed = cleanup(settings, tracker, now=now)
        tracker.set_meta("cleaned_at", iso(now))
        if freed["files"]:
            done.append(f"cleaned up {freed['files']} old file(s), {freed['bytes'] / 1e6:.0f} MB")
    if notify.enabled(settings) and digest_due(settings, tracker, now):
        days = 1 if settings.digest == "daily" else 7
        if notify.send(settings, digest_text(settings, tracker, days=days, now=now), session):
            mark_digest_sent(settings, tracker, now)
            done.append("digest sent")
    return done
