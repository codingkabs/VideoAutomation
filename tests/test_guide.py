"""The creator guide covers every platform and reaches the CLI and web app."""

import pytest

from videoautomation import cli
from videoautomation.config import Settings, load_guide, load_platforms

REQUIRED = ("summary", "how_it_works", "length", "best_times", "tips", "earn")


def test_every_platform_has_a_complete_guide():
    platforms, guide = load_platforms(), load_guide()
    assert set(guide) == set(platforms), f"missing: {set(platforms) - set(guide)}, extra: {set(guide) - set(platforms)}"
    for key, entry in guide.items():
        missing = [f for f in REQUIRED if not entry.get(f)]
        assert not missing, f"{key} guide is missing {missing}"
        assert isinstance(entry["tips"], list) and all(isinstance(t, str) and t for t in entry["tips"]), key
        for field in REQUIRED[:-2]:
            assert isinstance(entry[field], str) and len(entry[field]) < 600, f"{key}.{field} should be short text"


def test_uk_core_guides_mention_the_numbers_that_matter():
    g = load_guide()
    assert "5 hashtags" in " ".join(g["instagram"]["tips"])
    assert "1 min" in g["tiktok"]["earn"] and "drafts" in " ".join(g["tiktok"]["tips"])
    assert "3 minutes" in g["youtube"]["length"]


def test_cli_platform_detail(monkeypatch, capsys, settings):
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    assert cli.main(["platforms", "instagram"]) == 0
    out = capsys.readouterr().out
    assert "How posts get seen" in out and "When to post" in out and "In vauto" in out and "Route: meta" in out
    assert cli.main(["platforms", "tumblr"]) == 0
    assert "No posting route yet" in capsys.readouterr().out
    assert cli.main(["platforms", "myspace"]) == 2
    assert "Unknown platform" in capsys.readouterr().err
    cli.main(["platforms", "--tier", "1"])
    assert "vauto platforms NAME" in capsys.readouterr().out


def test_web_status_carries_the_guide(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    client = create_app(settings).test_client()
    rows = {p["key"]: p for p in client.get("/api/status").get_json()["platforms"]}
    assert rows["tiktok"]["guide"]["best_times"] and rows["instagram"]["rate_limit"]
    html = client.get("/").get_data(as_text=True)
    assert 'id="platform-list"' in html and 'id="plat-q"' in html
