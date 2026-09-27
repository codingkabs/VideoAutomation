"""When to post: parse `--at` values and pick the next best posting slot.

`--at` accepts: now, best, 18:30, 6pm, tomorrow 9am, +90m, +2h, +1d,
2026-10-01 18:30, or a full ISO time. Times without a zone use VAUTO_TIMEZONE
(default Europe/London).

`best` picks the next slot from VAUTO_BEST_TIMES (UK engagement peaks by
default), or from Zernio's best-time analytics when ZERNIO_PROFILE_ID is set.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .errors import ConfigError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_LEAD = timedelta(minutes=10)


def zone(settings: Settings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown VAUTO_TIMEZONE {settings.timezone!r} (use e.g. Europe/London)") from exc


def _clock(text: str) -> time:
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text.strip().lower())
    if not m:
        raise ConfigError(f"Could not read the time {text!r} (try 18:30 or 6pm)")
    hour, minute, meridiem = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ConfigError(f"Invalid time {text!r}")
    return time(hour, minute)


def parse_slots(spec: str) -> dict[int, list[time]]:
    """'mon-fri 07:30,12:30; sat-sun 10:00' -> {0: [07:30, 12:30], ..., 5: [10:00], 6: [10:00]}."""
    slots: dict[int, list[time]] = {d: [] for d in range(7)}
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        days_text, _, times_text = part.partition(" ")
        days: list[int] = []
        for chunk in days_text.lower().split(","):
            if "-" in chunk:
                a, b = chunk.split("-", 1)
                ia, ib = DAYS.index(a[:3]), DAYS.index(b[:3])
                days += list(range(ia, ib + 1)) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
            elif chunk[:3] in DAYS:
                days.append(DAYS.index(chunk[:3]))
            else:
                raise ConfigError(f"Bad day {chunk!r} in VAUTO_BEST_TIMES")
        for day in days:
            slots[day] += [_clock(t) for t in times_text.split(",") if t.strip()]
    for day in slots:
        slots[day] = sorted(set(slots[day]))
    if not any(slots.values()):
        raise ConfigError("VAUTO_BEST_TIMES has no times")
    return slots


def next_slot(slots: dict[int, list[time]], now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    for offset in range(8):
        day = (local + timedelta(days=offset)).date()
        for t in slots.get(day.weekday(), []):
            candidate = datetime.combine(day, t, tzinfo=tz)
            if candidate - now >= MIN_LEAD:
                return candidate.astimezone(timezone.utc)
    raise ConfigError("No upcoming slot in VAUTO_BEST_TIMES")


def best_time(settings: Settings, now: datetime | None = None, platform: str = "instagram",
              zernio_hours: list[tuple[int, int]] | None = None) -> tuple[datetime, str]:
    """Next best slot and where it came from."""
    now = now or datetime.now(timezone.utc)
    tz = zone(settings)
    if zernio_hours:
        slots: dict[int, list[time]] = {d: [] for d in range(7)}
        for day, hour in zernio_hours[:21]:
            slots[day].append(time(hour, 0))
        return next_slot(slots, now, tz), f"Zernio best-time analytics for {platform}"
    return next_slot(parse_slots(settings.best_times), now, tz), "VAUTO_BEST_TIMES"


def parse_when(text: str | None, settings: Settings, now: datetime | None = None) -> datetime | None:
    """Return a UTC datetime, or None for 'post now'. 'best' must be resolved with best_time()."""
    if text is None or text.strip().lower() in ("", "now"):
        return None
    now = now or datetime.now(timezone.utc)
    tz = zone(settings)
    raw = text.strip().lower()
    if raw == "best":
        raise ConfigError("resolve 'best' with best_time()")

    rel = re.fullmatch(r"\+\s*(\d+(?:\.\d+)?)\s*(m|min|mins|h|hr|hrs|d|day|days)", raw)
    if rel:
        amount, unit = float(rel.group(1)), rel.group(2)[0]
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return now + delta

    local_now = now.astimezone(tz)
    if raw.startswith(("today", "tomorrow")):
        word, _, clock_text = raw.partition(" ")
        day = local_now.date() + timedelta(days=1 if word == "tomorrow" else 0)
        when = datetime.combine(day, _clock(clock_text or "09:00"), tzinfo=tz)
    else:
        try:
            parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
            when = parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except ValueError:
            clock = _clock(raw)
            when = datetime.combine(local_now.date(), clock, tzinfo=tz)
            if when <= local_now:
                when += timedelta(days=1)
    when = when.astimezone(timezone.utc)
    if when <= now:
        raise ConfigError(f"{text!r} is in the past")
    return when
