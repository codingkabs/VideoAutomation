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
