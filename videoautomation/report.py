"""Plain-text and JSON output for post results."""

from __future__ import annotations

import json
from datetime import datetime

from .models import PostResult

ICONS = {
    "published": "OK", "scheduled": "SCHEDULED", "queued": "QUEUED", "draft": "DRAFT",
    "submitted": "SUBMITTED", "failed": "FAILED", "skipped": "SKIPPED", "dry_run": "DRY RUN",
    "duplicate": "ALREADY DONE",
}


def _when(run_at: str | None) -> str:
    if not run_at:
        return ""
    try:
        dt = datetime.fromisoformat(run_at.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %H:%M %Z")
    except ValueError:
        return run_at


def format_results(results: list[PostResult], notes: list[str] | None = None) -> str:
    rows = []
    for r in results:
        label = r.platform if r.surface in ("reel", "video", "short", "spotlight") else f"{r.platform} {r.surface}"
        detail = r.url or r.error or ""
        if r.status in ("scheduled", "queued") or (r.status == "dry_run" and r.run_at):
            detail = f"at {_when(r.run_at)}" + (f"  {detail}" if detail else "")
        rows.append((label, ICONS.get(r.status, r.status.upper()), detail, r.notes))

    width = max((len(r[0]) for r in rows), default=8)
    status_w = max((len(r[1]) for r in rows), default=6)
    lines = []
    for label, status, detail, row_notes in rows:
        lines.append(f"{label:<{width}}  {status:<{status_w}}  {detail}".rstrip())
        for note in row_notes:
            lines.append(f"{'':<{width}}  {'':<{status_w}}  - {note}")
    if notes:
        lines.append("")
        lines.extend(f"note: {n}" for n in notes)
    return "\n".join(lines)


def results_json(post_id: str, results: list[PostResult], notes: list[str]) -> str:
    return json.dumps({"post_id": post_id, "results": [r.to_dict() for r in results], "notes": notes}, indent=2)
