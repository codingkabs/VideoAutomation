import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from videoautomation import timing
from videoautomation.config import Settings, load_platforms, postable_platforms
from videoautomation.envfile import update_env
from videoautomation.errors import ConfigError
from videoautomation.fields import ALL_FIELDS

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------ platform specs


@pytest.mark.parametrize("name", postable_platforms())
def test_postable_platform_specs_are_complete(name):
    spec = load_platforms()[name]
    for key in ("name", "tier", "backends", "default_backend", "surfaces", "video", "caption"):
        assert key in spec, f"{name} is missing {key}"
    assert spec["default_backend"] in spec["backends"]
    assert spec["surfaces"].get("video")
    if spec["surfaces"].get("photos"):
        assert spec.get("photos") and spec["photos"]["max_items"] >= 1
    if "zernio" in spec["backends"]:
        assert spec.get("zernio_platform"), f"{name} needs zernio_platform"
    if "sau" in spec["backends"]:
        assert spec.get("sau_platform"), f"{name} needs sau_platform"
    assert spec["caption"].get("max_chars")


def test_catalogue_covers_about_fifty_platforms():
    assert len(load_platforms()) >= 50
    assert len(postable_platforms()) >= 40


def test_every_setting_is_in_env_example():
    example = (ROOT / ".env.example").read_text()
    keys = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", example, re.M))
    missing = sorted(set(ALL_FIELDS) - keys)
    assert not missing, f".env.example is missing {missing}"


def test_backend_override_must_be_allowed(tmp_path):
    with pytest.raises(ConfigError):
        Settings.from_env(env={"VAUTO_HOME": str(tmp_path), "VAUTO_BACKEND_X": "meta"}, dotenv=None)
    s = Settings.from_env(env={"VAUTO_HOME": str(tmp_path), "VAUTO_BACKEND_TIKTOK": "tiktok"}, dotenv=None)
    assert s.backends["tiktok"] == "tiktok"


def test_dotenv_round_trip(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nZERNIO_API_KEY=old\n")
    update_env(env, {"ZERNIO_API_KEY": "new key", "IG_USER_ID": "123", "VAUTO_HOOK": 'say "hi"'})
    s = Settings.from_env(env={"VAUTO_HOME": str(tmp_path)}, dotenv=env)
    assert s.zernio_api_key == "new key" and s.ig_user_id == "123"
    assert s.env["VAUTO_HOOK"] == 'say "hi"'
    assert env.read_text().startswith("# comment\n")


# -------------------------------------------------------------------- timing

NOW = datetime(2026, 9, 27, 17, 0, tzinfo=timezone.utc)  # Sunday 18:00 London (BST)


@pytest.fixture
def s(tmp_path):
    return Settings.from_env(env={"VAUTO_HOME": str(tmp_path)}, dotenv=None)


def london(dt):
    return dt.astimezone(timing.zone(Settings.from_env(env={}, dotenv=None)))


def test_parse_now_and_relative(s):
    assert timing.parse_when(None, s, NOW) is None
    assert timing.parse_when("now", s, NOW) is None
    assert timing.parse_when("+90m", s, NOW) == NOW + timedelta(minutes=90)
    assert timing.parse_when("+2h", s, NOW) == NOW + timedelta(hours=2)
    assert timing.parse_when("+1d", s, NOW) == NOW + timedelta(days=1)


def test_parse_clock_times(s):
    assert london(timing.parse_when("18:30", s, NOW)).strftime("%a %H:%M") == "Sun 18:30"
    assert london(timing.parse_when("9am", s, NOW)).strftime("%a %H:%M") == "Mon 09:00"  # passed today
    assert london(timing.parse_when("6pm", s, NOW)).strftime("%a %H:%M") == "Mon 18:00"
    assert london(timing.parse_when("tomorrow 7:15am", s, NOW)).strftime("%a %H:%M") == "Mon 07:15"
    assert london(timing.parse_when("2026-10-01 20:00", s, NOW)).strftime("%d %H:%M") == "01 20:00"


def test_parse_rejects_past_and_garbage(s):
    with pytest.raises(ConfigError):
        timing.parse_when("2020-01-01 10:00", s, NOW)
    with pytest.raises(ConfigError):
        timing.parse_when("whenever", s, NOW)


def test_slots_and_best_time(s):
    slots = timing.parse_slots("mon-fri 07:30,12:30; sat-sun 10:00,19:30")
    assert [t.strftime("%H:%M") for t in slots[0]] == ["07:30", "12:30"]
    assert [t.strftime("%H:%M") for t in slots[6]] == ["10:00", "19:30"]
    when, source = timing.best_time(s, NOW)
    assert london(when).strftime("%a %H:%M") == "Sun 19:30"
    assert source == "VAUTO_BEST_TIMES"


def test_best_time_uses_zernio_hours(s):
    when, source = timing.best_time(s, NOW, zernio_hours=[(0, 14), (6, 21)])
    assert london(when).strftime("%a %H:%M") == "Sun 21:00"
    assert "Zernio" in source


def test_best_time_skips_slots_too_soon(s):
    almost = datetime(2026, 9, 27, 18, 25, tzinfo=timezone.utc)  # 19:25 London, slot 19:30 is too close
    when, _ = timing.best_time(s, almost)
    assert london(when).strftime("%a %H:%M") == "Mon 07:30"


def test_env_example_loads_as_is(tmp_path):
    env = tmp_path / ".env"
    env.write_text((ROOT / ".env.example").read_text())
    s = Settings.from_env(env={"VAUTO_HOME": str(tmp_path)}, dotenv=env)
    assert s.default_platforms == ["instagram", "facebook", "tiktok", "youtube", "snapchat"]
    assert s.trial_delay_minutes == (60, 120) and s.telegram_allowed_user_ids == []


def test_slots_without_days_apply_every_day():
    slots = timing.parse_slots("09:00, 12:30,21:00", "VAUTO_TRIAL_SLOTS")
    assert all([t.strftime("%H:%M") for t in slots[d]] == ["09:00", "12:30", "21:00"] for d in range(7))
    with pytest.raises(ConfigError, match="VAUTO_TRIAL_SLOTS"):
        timing.parse_slots("someday 09:00", "VAUTO_TRIAL_SLOTS")


def test_free_slot_skips_taken_and_rolls_over(s):
    tz = timing.zone(s)
    slots = timing.parse_slots("09:00,12:30,21:00")
    taken = [datetime(2026, 9, 27, 20, 10, tzinfo=timezone.utc)]  # 21:10 London holds the 21:00 slot
    when = timing.free_slot(slots, NOW, tz, taken)
    assert london(when).strftime("%a %H:%M") == "Mon 09:00"
    assert london(timing.free_slot(slots, NOW, tz, [])).strftime("%a %H:%M") == "Sun 21:00"


def test_slot_times_from_the_queue(s):
    from videoautomation import service
    from videoautomation.models import MediaFile, PostJob
    from videoautomation.scheduler import JobStore, iso, parse_iso

    service._held_slots.clear()
    now = datetime(2026, 9, 28, 7, 0, tzinfo=timezone.utc)  # Monday 08:00 London
    first = service.slot_time(s, "trial", now=now)
    assert london(first).strftime("%H:%M") == "09:00"

    job = PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], "c",
                  post_id="p1", idem_key="k1", run_at=iso(first))
    JobStore(s.db_path).insert(job, status="pending")
    assert london(service.slot_time(s, "trial", now=now)).strftime("%H:%M") == "12:30"
    # A slot handed to a real post is held while it renders, even before it is queued.
    held = service.slot_time(s, "trial", now=now, reserve=True)
    assert london(held).strftime("%H:%M") == "12:30"
    assert london(service.slot_time(s, "trial", now=now)).strftime("%H:%M") == "21:00"
    # A main post's trial comes after the main post.
    main, trial, notes = service.resolve_times(s, "slot:main", "slot:trial", ["instagram"], dry_run=True)
    assert parse_iso(trial) > parse_iso(main) and len(notes) == 2
    service._held_slots.clear()


def test_day_slots_show_what_holds_each(s):
    from datetime import date

    from videoautomation import service
    from videoautomation.models import MediaFile, PostJob
    from videoautomation.scheduler import JobStore, iso

    tz = timing.zone(s)
    at = datetime(2026, 9, 28, 12, 30, tzinfo=tz)
    JobStore(s.db_path).insert(PostJob("instagram", "trial_reel", "zernio", [MediaFile("x.mp4", "video")], "c",
                                       post_id="p1", idem_key="k1", run_at=iso(at)), status="pending")
    day = service.day_slots(s, date(2026, 9, 28))
    assert [(d["kind"], d["local"]) for d in day] == [("trial", "09:00"), ("trial", "12:30"), ("main", "18:00"),
                                                      ("trial", "21:00")]
    assert day[1]["taken_by"]["post_id"] == "p1" and day[0]["taken_by"] is None
