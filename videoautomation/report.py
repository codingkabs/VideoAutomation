"""Plain-text (optionally coloured) and JSON output for results and checks."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

from .models import PostResult, label_for

STATUS_TEXT = {
    "published": "POSTED", "scheduled": "SCHEDULED", "queued": "QUEUED", "draft": "DRAFT",
    "submitted": "SUBMITTED", "failed": "FAILED", "skipped": "SKIPPED", "dry_run": "PREVIEW",
    "duplicate": "ALREADY DONE", "handoff": "TO PHONE", "reported": "REPORTED",
}
COLOURS = {
    "published": "32", "reported": "32", "draft": "36", "handoff": "36", "scheduled": "33", "queued": "33",
    "submitted": "33", "dry_run": "34", "duplicate": "90", "skipped": "90", "failed": "31",
    "ok": "32", "warn": "33", "fail": "31", "off": "90",
}
CHECK_MARK = {"ok": "✓", "warn": "!", "fail": "✗", "off": "·"}


def use_colour(stream=None) -> bool:
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def paint(text: str, status: str, colour: bool) -> str:
    code = COLOURS.get(status)
    return f"\033[{code}m{text}\033[0m" if colour and code else text


def when(run_at: str | None) -> str:
    if not run_at:
        return ""
    try:
        dt = datetime.fromisoformat(run_at.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%a %d %b %H:%M")
    except ValueError:
        return run_at


def format_results(results: list[PostResult], notes: list[str] | None = None, colour: bool = False) -> str:
    rows = []
    for r in results:
        detail = r.url or r.error or ""
        if r.run_at and r.status in ("scheduled", "queued", "dry_run"):
            detail = f"at {when(r.run_at)}" + (f"  {detail}" if detail else "")
        rows.append((label_for(r.platform, r.surface), r.status, STATUS_TEXT.get(r.status, r.status.upper()),
                     detail, r.notes))
    width = max((len(r[0]) for r in rows), default=8)
    status_w = max((len(r[2]) for r in rows), default=6)
    lines = []
    for label, status, text, detail, row_notes in rows:
        padded = paint(f"{text:<{status_w}}", status, colour)
        lines.append(f"{label:<{width}}  {padded}  {detail}".rstrip())
        for note in row_notes:
            lines.append(f"{'':<{width}}  {'':<{status_w}}  - {note}")
    if notes:
        lines.append("")
        lines.extend(f"note: {n}" for n in notes)
    return "\n".join(lines)


def summary_line(results: list[PostResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    order = ("published", "draft", "handoff", "scheduled", "queued", "dry_run", "duplicate", "skipped", "failed")
    parts = [f"{counts[s]} {STATUS_TEXT.get(s, s).lower()}" for s in order if counts.get(s)]
    return ", ".join(parts)


def format_checks(checks, colour: bool = False) -> str:
    lines, area = [], None
    width = max((len(c.name) for c in checks), default=10)
    for c in checks:
        if c.area != area:
            area = c.area
            lines.append(("" if not lines else "\n") + area.upper())
        mark = paint(CHECK_MARK.get(c.status, "?"), c.status, colour)
        line = f"  {mark} {c.name:<{width}}  {c.detail}"
        if c.fix:
            line += paint(f"  (fix: {c.fix})", "off", colour)
        lines.append(line)
    return "\n".join(lines)


def results_json(post_id: str, results: list[PostResult], notes: list[str]) -> str:
    return json.dumps({"post_id": post_id, "results": [r.to_dict() for r in results], "notes": notes}, indent=2)
