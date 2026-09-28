"""vauto web app: upload, preview, post, queue, trial results and setup in one page.

Runs locally (http://127.0.0.1:8765). To open it from your phone on the same
network, start it with --host 0.0.0.0 and set VAUTO_WEB_PASSWORD.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .. import service
from ..config import Settings, load_platforms, platform_guide, platform_spec
from ..doctor import platform_ready, run_checks
from ..envfile import update_env
from ..errors import ConfigError, VautoError
from ..ffmpeg import probe, run_ffmpeg
from ..fields import ALL_FIELDS, GROUPS, mask
from ..media.variants import VariantOptions
from ..models import label_for
from ..scheduler import JobStore
from ..timing import zone
from ..tracker import Tracker

STATIC = Path(__file__).parent / "static"
LOOPBACK = ("127.0.0.1", "localhost", "::1")
TASK_TTL = 6 * 3600
JSON_EXEMPT = ("/api/upload", "/api/subtitles")  # file uploads; every other API write must be JSON


def create_app(settings: Settings):
    try:
        from flask import Flask, Response, abort, jsonify, redirect, request, send_from_directory
    except ImportError as exc:
        raise ConfigError("The web app needs Flask: pip install 'vauto[web]'") from exc
    from werkzeug.utils import secure_filename

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 ** 3
    state: dict[str, Any] = {"settings": settings}
    tasks: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def cfg() -> Settings:
        return state["settings"]

    uploads = cfg().home / "uploads"

    # ------------------------------------------------------------- security
    def own_hosts() -> set[str]:
        """Addresses this app is reached on. A proxy such as `tailscale serve` may pass
        its own name in X-Forwarded-Host, and VAUTO_PUBLIC_URL is the phone address."""
        hosts = {request.host}
        forwarded = request.headers.get("X-Forwarded-Host")
        if forwarded:
            hosts.add(forwarded.split(",")[0].strip())
        public = (cfg().env.get("VAUTO_PUBLIC_URL") or "").strip().rstrip("/")
        if public:
            hosts.add(public.split("://", 1)[-1].split("/", 1)[0])
        return hosts

    @app.before_request
    def guard():
        password = cfg().web_password
        host = (request.host or "").rsplit(":", 1)[0].strip("[]")
        if not password and host not in LOOPBACK:
            # Blocks DNS-rebinding: without a password only localhost names are served.
            return Response("Open vauto at http://127.0.0.1 or set VAUTO_WEB_PASSWORD", 403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in own_hosts() and request.path != "/share":
                return Response("Cross-site request blocked", 403)  # CSRF guard
            # Writes with a body must be JSON (a cross-site form cannot send that without CORS).
            # DELETE carries no body; the Origin check above covers it.
            if request.method in ("POST", "PUT", "PATCH") and request.path.startswith("/api/") \
                    and request.path not in JSON_EXEMPT and not request.is_json:
                return Response("Expected JSON", 415)
        if not password:
            return None
        auth = request.authorization
        if auth and auth.password and hmac.compare_digest(auth.password, password):
            return None
        return Response("Password required", 401, {"WWW-Authenticate": 'Basic realm="vauto"'})

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    @app.errorhandler(VautoError)
    def vauto_error(exc):
        return jsonify({"error": str(exc)}), 400

    # ---------------------------------------------------------------- pages
    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name: str):
        return send_from_directory(STATIC, name)

    @app.get("/media/<post_id>/<path:name>")
    def media(post_id: str, name: str):
        return send_from_directory(cfg().renders_dir / secure_filename(post_id), name)

    @app.get("/uploads/<upload_id>/<path:name>")
    def uploaded(upload_id: str, name: str):
        return send_from_directory(uploads / secure_filename(upload_id), name)

    def media_url(path: str) -> str | None:
        p = Path(path)
        for base, prefix in ((cfg().renders_dir, "/media"), (uploads, "/uploads")):
            try:
                rel = p.resolve().relative_to(base.resolve())
            except ValueError:
                continue
            return f"{prefix}/{rel.as_posix()}"
        return None

    def poster_url(path: str) -> str | None:
        """A JPEG frame to show before a video plays (cached next to the video)."""
        video = Path(path)
        poster = video.with_name(video.stem + ".poster.jpg")
        if not poster.is_file():
            for offset in ("1", "0"):
                try:
                    run_ffmpeg(["-ss", offset, "-i", str(video), "-frames:v", "1", "-vf", "scale=540:-2",
                                "-q:v", "4", str(poster)])
                except VautoError:
                    continue
                if poster.is_file() and poster.stat().st_size:
                    break
        return media_url(str(poster)) if poster.is_file() else None

    def media_item(kind: str, path: str) -> dict[str, Any]:
        return {"kind": kind, "url": media_url(path), "poster": poster_url(path) if kind == "video" else None}

    # --------------------------------------------------------------- status
    def platform_rows() -> list[dict[str, Any]]:
        s = cfg()
        rows = []
        for key, spec in load_platforms().items():
            status = spec.get("status", "planned")
            ready, detail = platform_ready(s, key) if status != "planned" else (False, spec.get("route", ""))
            rows.append({
                "key": key, "name": spec["name"], "tier": spec.get("tier"), "status": status,
                "regions": spec.get("regions", []), "backend": s.backends.get(key), "ready": ready,
                "detail": detail, "backends": spec.get("backends", []),
                "caption": spec.get("caption") or {}, "video": spec.get("video") or {},
                "photos": spec.get("photos"), "default": key in s.default_platforms,
                "guide": platform_guide(key), "rate_limit": spec.get("rate_limit"),
            })
        return rows

    @app.get("/api/status")
    def status():
        s = cfg()
        try:
            _, best = service.resolve_publish_at(s, "best", s.default_platforms)
        except VautoError as exc:
            best = str(exc)
        return jsonify({
            "platforms": platform_rows(),
            "defaults": {
                "trial": s.trial_default, "trial_delay": list(s.trial_delay_minutes),
                "graduation": s.trial_graduation, "timezone": s.timezone, "subtitle_style": s.subtitle_style,
                "best_time": best, "claude": bool(s.anthropic_api_key),
            },
        })

    @app.get("/api/checks")
    def checks():
        online = request.args.get("online") == "1"
        return jsonify([c.to_dict() for c in run_checks(cfg(), online=online)])

    # --------------------------------------------------------------- upload
    def public_base() -> str:
        """The address your phone uses: VAUTO_PUBLIC_URL, else what the request came in on."""
        configured = (cfg().env.get("VAUTO_PUBLIC_URL") or "").rstrip("/")
        if configured:
            return configured
        scheme = "https" if request.headers.get("X-Forwarded-Proto") == "https" else request.scheme
        return f"{scheme}://{request.host}"

    def describe_upload(upload_id: str) -> dict[str, Any]:
        folder = uploads / secure_filename(upload_id)
        if not upload_id or not folder.is_dir():
            raise VautoError("That upload no longer exists; add the video again")
        out = []
        for dest in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".poster.jpg")):
            info = probe(dest)
            kind = "image" if info.is_image else "video"
            out.append({"name": dest.name.split("_", 1)[-1], "kind": kind, "width": info.width,
                        "height": info.height, "duration": round(info.duration, 2), **media_item(kind, str(dest))})
        return {"upload_id": folder.name, "files": out}

    def store_upload(files) -> dict[str, Any]:
        files = [f for f in files if f and f.filename]
        if not files:
            raise VautoError("No files received")
        upload_id = uuid.uuid4().hex[:12]
        folder = uploads / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(files):
            dest = folder / f"{i:02d}_{secure_filename(f.filename) or 'file'}"
            f.save(dest)
            try:
                probe(dest)
            except VautoError as exc:
                raise VautoError(f"{f.filename}: {exc}") from exc
        return describe_upload(upload_id)

    @app.post("/api/upload")
    def upload():
        return jsonify(store_upload(request.files.getlist("files")))

    @app.get("/api/uploads/<upload_id>")
    def get_upload(upload_id: str):
        return jsonify(describe_upload(upload_id))

    @app.post("/share")
    def share():
        """Share target for the installed app (Android) and the iPhone Shortcut.

        Stores the shared video/photos and opens the Post screen with them loaded.
        Nothing is posted until you press Post.
        """
        from urllib.parse import urlencode

        files = request.files.getlist("files") or request.files.getlist("media")
        data = store_upload(files)
        caption = (request.form.get("text") or request.form.get("title") or "").strip()
        query = {"upload": data["upload_id"]}
        if caption:
            query["caption"] = caption
        target = "/?" + urlencode(query) + "#post"
        if request.args.get("format") == "json":
            return jsonify({**data, "open_url": public_base() + target})
        return redirect(target, code=303)

    @app.get("/manifest.webmanifest")
    def manifest():
        return send_from_directory(STATIC, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        resp = send_from_directory(STATIC, "sw.js", mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.post("/api/subtitles")
    def upload_subtitles():
        f = request.files.get("file")
        if not f:
            raise VautoError("No subtitle file received")
        folder = uploads / "subtitles"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{uuid.uuid4().hex[:8]}_{secure_filename(f.filename or 'subs.srt')}"
        f.save(dest)
        return jsonify({"path": str(dest)})

    # ---------------------------------------------------------------- tasks
    def build_request(body: dict[str, Any], dry_run: bool, progress) -> tuple[Any, str | None]:
        s = cfg()
        upload_id = secure_filename(str(body.get("upload_id", "")))
        folder = uploads / upload_id
        if not upload_id or not folder.is_dir():
            raise VautoError("Upload the video or photos first")
        inputs = sorted(p for p in folder.iterdir() if p.is_file() and not p.name.endswith(".poster.jpg"))
        platforms = body.get("platforms") or s.default_platforms
        o = body.get("options") or {}
        publish_at, note = service.resolve_publish_at(s, o.get("at") or None, platforms)
        subtitles = o.get("subtitles") or None
        if subtitles not in (None, "auto"):
            sub_path = Path(subtitles).resolve()
            if (uploads / "subtitles").resolve() not in sub_path.parents:
                raise VautoError("Upload the subtitle file first")
        delay = o.get("trial_delay")
        req = service.make_request(
            s, inputs, body.get("caption", ""), platforms,
            caption_overrides={k: v for k, v in (body.get("caption_overrides") or {}).items() if v},
            publish_at=publish_at,
            trial=bool(o.get("trial")),
            trial_delay=tuple(int(x) for x in delay) if delay else None,
            trial_graduation=o.get("graduation"),
            trial_caption=o.get("trial_caption") or None,
            variant=VariantOptions(mode=o.get("trial_mode") or "static", zoom=float(o.get("trial_zoom") or 1.15),
                                   mirror=bool(o.get("trial_mirror")), speed=float(o.get("trial_speed") or 1.0),
                                   hook_text=o.get("trial_hook") or None, font_path=s.font_path),
            tiktok_draft=bool(o.get("tiktok_draft")), ig_story=bool(o.get("ig_story")),
            fit=o.get("fit") or "auto", allow_trim=bool(o.get("trim")),
            subtitles=subtitles, subtitle_style=o.get("subtitle_style") or None,
            rewrite_captions=bool(o.get("rewrite")), dry_run=dry_run, force=bool(o.get("force")),
            progress=progress,
        )
        return req, note

    def run_task(task_id: str, body: dict[str, Any], dry_run: bool) -> None:
        task = tasks[task_id]

        def progress(message: str) -> None:
            task["progress"].append(message)

        try:
            req, note = build_request(body, dry_run, progress)
            plan, results = service.post(cfg(), req)
            if note:
                plan.notes.insert(0, note)
            jobs = []
            for job in plan.jobs:
                if job.surface == "trial_report":
                    continue
                spec = platform_spec(job.platform)
                jobs.append({
                    "platform": job.platform, "name": spec["name"], "label": label_for(job.platform, job.surface),
                    "surface": job.surface, "backend": job.backend, "caption": job.caption,
                    "title": job.options.get("title"), "tags": job.options.get("tags") or [],
                    "run_at": job.run_at, "caption_limit": (spec.get("caption") or {}).get("max_chars"),
                    "media": [media_item(m.kind, m.path) for m in job.media],
                })
            task.update(state="done", result={
                "post_id": plan.post_id, "kind": plan.kind, "notes": plan.notes, "jobs": jobs,
                "results": [{**r.to_dict(), "label": label_for(r.platform, r.surface)} for r in results],
            })
            if not dry_run:
                service.notify_results(cfg(), results, f"vauto post {plan.post_id}")
        except VautoError as exc:
            task.update(state="error", error=str(exc))
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            task.update(state="error", error=f"{type(exc).__name__}: {exc}")

    def background(kind: str, fn) -> str:
        """Run ``fn(progress)`` in a thread; poll /api/tasks/<id> for the result."""
        now = time.time()
        with lock:
            for key in [k for k, t in tasks.items() if now - t["created"] > TASK_TTL]:
                tasks.pop(key, None)
            task_id = secrets.token_hex(6)
            tasks[task_id] = {"state": "running", "progress": [], "created": now, "action": kind}

        def work() -> None:
            task = tasks[task_id]
            try:
                task.update(state="done", result=fn(task["progress"].append))
            except VautoError as exc:
                task.update(state="error", error=str(exc))
            except Exception as exc:  # pragma: no cover - surfaced to the UI
                task.update(state="error", error=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=work, daemon=True).start()
        return task_id

    @app.post("/api/tasks")
    def start_task():
        body = request.get_json() or {}
        action = body.get("action")
        if action not in ("preview", "post"):
            raise VautoError("action must be preview or post")
        now = time.time()
        with lock:
            for key in [k for k, t in tasks.items() if now - t["created"] > TASK_TTL]:
                tasks.pop(key, None)
            task_id = secrets.token_hex(6)
            tasks[task_id] = {"state": "running", "progress": [], "created": now, "action": action}
        threading.Thread(target=run_task, args=(task_id, body, action == "preview"), daemon=True).start()
        return jsonify({"task_id": task_id})

    @app.get("/api/tasks/<task_id>")
    def get_task(task_id: str):
        task = tasks.get(task_id)
        if task is None:
            return jsonify({"error": "unknown task"}), 404
        return jsonify({k: v for k, v in task.items() if k != "created"})

    # ------------------------------------------------------------- my posts
    def tracker() -> Tracker:
        return Tracker.open(cfg())

    def post_view(p: dict[str, Any]) -> dict[str, Any]:
        out = dict(p)
        out["thumb"] = media_url(p["thumb"]) if p.get("thumb") else None
        out.pop("inputs", None)  # local file paths stay on this computer
        return out

    @app.get("/api/posts")
    def list_posts():
        a = request.args
        data = tracker().posts(cfg(), query=a.get("q", ""), platform=a.get("platform", ""),
                               show=a.get("show", "all"), limit=min(int(a.get("limit", 30)), 200),
                               offset=int(a.get("offset", 0)))
        return jsonify({"total": data["total"], "posts": [post_view(p) for p in data["posts"]]})

    @app.get("/api/posts/<post_id>")
    def get_post(post_id: str):
        t = tracker()
        p = post_view(t.describe(secure_filename(post_id), settings=cfg()))
        full = {r.idem_key[:10]: r.idem_key for r in t.store.rows(post_id=p["post_id"], limit=1000)}
        for j in p["jobs"]:
            j["history"] = t.history(full[j["id"]]) if j["id"] in full else []
        return jsonify(p)

    @app.delete("/api/posts/<post_id>")
    def forget_post(post_id: str):
        tracker().forget(secure_filename(post_id))
        return jsonify({"ok": True})

    @app.post("/api/posts/<post_id>/refresh")
    def refresh_post(post_id: str):
        from ..stats import refresh

        pid = secure_filename(post_id)
        return jsonify({"task_id": background("refresh", lambda _p: refresh(cfg(), tracker(), post_id=pid).to_dict())})

    @app.post("/api/stats/refresh")
    def refresh_all():
        from ..stats import refresh

        days = int((request.get_json() or {}).get("days") or 30)
        return jsonify({"task_id": background("refresh", lambda _p: refresh(cfg(), tracker(), days=days).to_dict())})

    @app.post("/api/posts/<post_id>/reuse")
    def reuse_post(post_id: str):
        """Load a post's video or photos into the Post screen again (post to more places, or repost)."""
        import shutil

        pid = secure_filename(post_id)
        t = tracker()
        row = t.post_row(pid) or {}
        sources = [Path(x) for x in json.loads(row.get("inputs") or "[]") if Path(x).is_file()]
        if not sources:
            work = cfg().renders_dir / pid
            masters = sorted(work.glob("master_*.mp4"), key=lambda x: ("subs" in x.name, x.name != "master_auto.mp4"))
            photos = sorted(work.glob("photo_instagram_*.jpg")) or sorted(work.glob("photo_*_*.jpg"))
            sources = masters[:1] or photos
        if not sources:
            raise VautoError("The original files are gone; add the video again")
        upload_id = uuid.uuid4().hex[:12]
        folder = uploads / upload_id
        folder.mkdir(parents=True, exist_ok=True)
        for i, src in enumerate(sources):
            name = src.name.split("_", 1)[-1] if src.parent.parent == uploads else src.name
            shutil.copy2(src, folder / f"{i:02d}_{secure_filename(name) or 'file'}")
        data = describe_upload(upload_id)
        data["caption"] = row.get("caption") or t.describe(pid)["caption"]
        return jsonify(data)

    @app.post("/api/jobs/<job_id>/posted")
    def job_posted(job_id: str):
        body = request.get_json() or {}
        return jsonify(tracker().mark_posted(secure_filename(job_id), body.get("url")).to_dict())

    @app.post("/api/jobs/<job_id>/numbers")
    def job_numbers(job_id: str):
        return jsonify({"metrics": tracker().set_numbers(secure_filename(job_id), request.get_json() or {})})

    @app.get("/api/summary")
    def summary():
        days = int(request.args.get("days", 30)) or None
        data = tracker().summary(cfg(), days=days)
        data["top"] = [post_view(p) for p in data["top"]]
        for j in data["upcoming"]:
            j["thumb"] = media_url(j["thumb"]) if j.get("thumb") else None
        data["timezone"] = str(zone(cfg()))
        return jsonify(data)

    @app.get("/api/export.csv")
    def export_csv():
        stamp = time.strftime("%Y-%m-%d")
        return Response(tracker().export_csv(cfg()), mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="vauto-posts-{stamp}.csv"'})

    @app.get("/api/snippets")
    def list_snippets():
        return jsonify(tracker().snippets())

    @app.post("/api/snippets")
    def add_snippet():
        body = request.get_json() or {}
        return jsonify(tracker().add_snippet(str(body.get("name", "")), str(body.get("text", ""))))

    @app.delete("/api/snippets/<int:snippet_id>")
    def delete_snippet(snippet_id: int):
        tracker().delete_snippet(snippet_id)
        return jsonify({"ok": True})

    # ----------------------------------------------------------------- jobs
    @app.get("/api/jobs")
    def jobs():
        if not cfg().db_path.is_file():
            return jsonify([])
        rows = JobStore(cfg().db_path).rows(limit=int(request.args.get("limit", 100)))
        return jsonify([{
            "id": r.idem_key[:10], "post_id": r.job.post_id, "platform": r.job.platform,
            "name": platform_spec(r.job.platform)["name"], "label": r.job.label, "surface": r.job.surface,
            "status": r.status, "run_at": r.run_at, "backend": r.job.backend,
            "url": r.result.url if r.result else None, "error": r.result.error if r.result else None,
            "notes": r.result.notes if r.result else [],
        } for r in rows])

    @app.post("/api/jobs/<job_id>/<action>")
    def job_action(job_id: str, action: str):
        if action == "cancel":
            result = service.cancel(cfg(), job_id)
        elif action == "retry":
            result = service.retry(cfg(), job_id)
        else:
            raise VautoError("unknown action")
        return jsonify(result.to_dict())

    @app.get("/api/trials")
    def trials():
        from ..insights import compare, trial_keys

        if not cfg().db_path.is_file():
            return jsonify([])
        store = JobStore(cfg().db_path)
        out = []
        for key in trial_keys(store, int(request.args.get("limit", 10))):
            try:
                out.append(compare(cfg(), store, key).to_dict())
            except VautoError as exc:
                out.append({"post_id": key[:12], "notes": [str(exc)], "lines": [str(exc)]})
        return jsonify(out)

    # ------------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        s = cfg()
        file_values = {}
        if s.dotenv_path and s.dotenv_path.is_file():
            from ..config import parse_dotenv

            file_values = parse_dotenv(s.dotenv_path)
        groups = []
        for gid, title, fields in GROUPS:
            items = []
            for f in fields:
                value = s.env.get(f.key, "")
                from_env = f.key in os.environ
                items.append({"key": f.key, "label": f.label, "help": f.help, "secret": f.secret,
                              "kind": f.kind, "choices": list(f.choices), "set": bool(value),
                              "value": mask(value) if f.secret else value, "locked": from_env,
                              "in_file": f.key in file_values})
            groups.append({"id": gid, "title": title, "fields": items})
        return jsonify({"path": str(s.dotenv_path) if s.dotenv_path else None, "groups": groups})

    @app.post("/api/settings")
    def save_settings():
        from ..config import parse_dotenv

        s = cfg()
        body = request.get_json() or {}
        path = s.dotenv_path or Path.cwd() / ".env"
        in_file = parse_dotenv(path)
        values = {}
        for key, value in (body.get("values") or {}).items():
            field = ALL_FIELDS.get(key)
            if field is None:
                continue
            value = "" if value is None else str(value).strip()
            if field.secret and value.startswith("•"):
                continue  # unchanged masked secret
            if "\n" in value:
                raise VautoError(f"{key} cannot contain a line break")
            if value == in_file.get(key, "") or (not value and key not in in_file):
                continue  # nothing to change
            values[key] = value
        changed = update_env(path, values)
        try:
            state["settings"] = Settings.from_env(dotenv=path) if s.dotenv_path else Settings.from_env()
        except VautoError as exc:
            return jsonify({"changed": changed, "error": f"Saved, but the settings have a problem: {exc}"}), 400
        return jsonify({"changed": changed})

    @app.get("/api/best-time")
    def best_time():
        _, note = service.resolve_publish_at(cfg(), "best", request.args.getlist("p") or cfg().default_platforms)
        return jsonify({"note": note, "timezone": str(zone(cfg()))})

    return app


def run(settings: Settings, host: str = "127.0.0.1", port: int = 8765) -> None:
    if host not in LOOPBACK and not settings.web_password:
        raise ConfigError("Set VAUTO_WEB_PASSWORD before opening the web app to other devices (--host)")
    app = create_app(settings)
    shown = "localhost" if host in LOOPBACK else host
    print(f"vauto web app: http://{shown}:{port}  (Ctrl+C to stop)")
    app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False, load_dotenv=False)
