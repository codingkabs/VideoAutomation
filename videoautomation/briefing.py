"""Morning briefing and main-slot reminders, sent to Telegram by the worker.

Briefing (VAUTO_BRIEFING, default 08:00): today's main and Trial Reel slots and what
holds each, plus how the last few days' Trial Reels did against your usual trial, with
the winners' links. Reminder (VAUTO_MAIN_REMINDER_HOURS, default 3): a nudge when the
main slot is coming up and nothing is in it yet.

Both read only vauto's own data; neither calls Instagram.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from . import timing, trials
from .config import Settings
from .scheduler import JobStore, parse_iso, utcnow

ICON = {"winner": "🏆", "below": "▫️", "average": "·", "early": "⏳", "no_history": "·", "no_numbers": "·",
        "waiting": "⏳"}


def _public(settings: Settings) -> str | None:
    return (settings.env.get("VAUTO_PUBLIC_URL") or "").rstrip("/") or None


def _clock(text: str):
    try:
        return timing._clock(text)
    except Exception:
        return None


def briefing_due(settings: Settings, last_sent: str | None, now: datetime | None = None) -> bool:
    """Once a day, at or after VAUTO_BRIEFING (local time)."""
    if settings.briefing_time in ("", "off", "none", "0"):
        return False
    at = _clock(settings.briefing_time)
    if at is None:
        return False
    local = (now or utcnow()).astimezone(timing.zone(settings))
    return (local.hour, local.minute) >= (at.hour, at.minute) and last_sent != local.date().isoformat()


def slot_lines(settings: Settings, now: datetime | None = None) -> list[str]:
    from .service import day_slots

    now = now or utcnow()
    day = now.astimezone(timing.zone(settings)).date()
    lines = []
    for slot in day_slots(settings, day):
        held = slot["taken_by"]
        if held:
            what = ("Trial Reel" if held["surface"] == "trial_reel" else "Reel") + f" · {held['status']} ✓"
        elif parse_iso(slot["time"]) <= now:
            what = "missed"
        elif slot["kind"] == "main":
            what = "free: share your video with Today's main"
        else:
            what = "free"
        lines.append(f"{slot['local']} {'Main ' if slot['kind'] == 'main' else 'Trial'}  {what}")
    return lines


def briefing_text(settings: Settings, store: JobStore, now: datetime | None = None) -> str:
    now = now or utcnow()
    local = now.astimezone(timing.zone(settings))
    lines = [f"☀️ {local:%a %d %b}: today's posts", *slot_lines(settings, now)]
    recent, base = trials.recent(store, days=4, now=now)
    judged = [r for r in recent if r.verdict not in ("waiting",)]
    if judged:
        lines.append("")
        lines.append("Trial Reels, last 4 days:")
        public = _public(settings)
        order = {"winner": 0, "average": 1, "early": 2, "no_history": 3, "below": 4, "no_numbers": 5}
        for r in sorted(judged, key=lambda r: (order.get(r.verdict, 9), r.run_at))[:6]:
            lines.append(f"{ICON.get(r.verdict, '·')} “{r.caption[:50]}”: {trials.describe(r, base)}")
            if r.verdict == "winner":
                if r.url:
                    lines.append(f"   Share it with followers: {r.url}")
                if public:
                    lines.append(f"   Or post it as a fresh main Reel: {public}/?reuse={r.post_id}")
        if base:
            lines.append(f"Your usual trial: {trials.compact(base['views'])} views, "
                         f"{base['engage_per_1k']:.1f} shares+saves per 1K ({base['trials']} trials)")
    return "\n".join(lines)


def main_reminders(settings: Settings, sent: set[str], now: datetime | None = None) -> list[tuple[str, str]]:
    """(slot time, message) for main slots coming up within VAUTO_MAIN_REMINDER_HOURS that are
    still empty and haven't been reminded about."""
    from .service import day_slots

    if settings.main_reminder_hours <= 0:
        return []
    now = now or utcnow()
    out = []
    local_day = now.astimezone(timing.zone(settings)).date()
    for day in (local_day, local_day + timedelta(days=1)):
        for slot in day_slots(settings, day):
            when = parse_iso(slot["time"])
            if slot["kind"] != "main" or slot["taken_by"] or slot["time"] in sent:
                continue
            if timedelta(minutes=10) < when - now <= timedelta(hours=settings.main_reminder_hours):
                public = _public(settings)
                text = (f"📣 Your {slot['local']} main slot is still empty. Share your video: Share → vauto, "
                        "switch on Today's main" + (f"\n{public}" if public else ""))
                out.append((slot["time"], text))
    return out


def run(settings: Settings, tracker, now: datetime | None = None, session=None) -> list[str]:
    """Send what's due. Called from the worker's housekeeping."""
    from . import notify

    if not notify.enabled(settings):
        return []
    now = now or utcnow()
    done = []
    if briefing_due(settings, tracker.get_meta("briefing_sent"), now):
        if notify.send(settings, briefing_text(settings, tracker.store, now), session):
            tracker.set_meta("briefing_sent", now.astimezone(timing.zone(settings)).date().isoformat())
            done.append("briefing sent")
    sent = set(filter(None, (tracker.get_meta("main_reminded") or "").split(",")))
    for slot_time, text in main_reminders(settings, sent, now):
        if notify.send(settings, text, session):
            sent.add(slot_time)
            done.append(f"reminded about the {slot_time} main slot")
    if done:
        # Keep the list short: only reminders for the last couple of days matter.
        keep = sorted(t for t in sent if parse_iso(t) >= now - timedelta(days=2))
        tracker.set_meta("main_reminded", ",".join(keep))
    return done
