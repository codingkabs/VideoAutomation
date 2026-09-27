import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from videoautomation import auth
from videoautomation.errors import ConfigError, MediaError, PublishError
from videoautomation.ffmpeg import probe
from videoautomation.media.normalize import normalize_video
from videoautomation.media.subtitles import (Cue, burn_subtitles, cues_for, cues_to_ass, load_cues,
                                             parse_subtitles, words_to_cues, write_srt)

from .conftest import FakeResponse, FakeSession, needs_ffmpeg

SRT = """1
00:00:00,500 --> 00:00:01,800
Hello there

2
00:00:02,000 --> 00:00:03,250
<i>Second</i> line
"""

VTT = """WEBVTT

00:00.500 --> 00:01.800 align:center
Hello there
"""


# ----------------------------------------------------------------- subtitles


def test_parse_srt_and_vtt():
    cues = parse_subtitles(SRT)
    assert cues == [Cue(0.5, 1.8, "Hello there"), Cue(2.0, 3.25, "Second line")]
    assert parse_subtitles(VTT) == [Cue(0.5, 1.8, "Hello there")]
    with pytest.raises(MediaError):
        parse_subtitles("no cues here")


def test_srt_round_trip(tmp_path):
    cues = parse_subtitles(SRT)
    assert load_cues(write_srt(cues, tmp_path / "x.srt")) == cues
    with pytest.raises(MediaError):
        load_cues(tmp_path / "x.txt")


def test_words_group_into_short_chunks():
    words = [(0.0, 0.3, "one"), (0.3, 0.6, "two"), (0.6, 0.9, "three,"), (0.9, 1.2, "four"),
             (1.2, 1.5, "five"), (1.5, 1.8, "six"), (1.8, 2.1, "seven"), (2.1, 2.4, "eight")]
    cues = words_to_cues(words, max_words=4)
    assert [c.text for c in cues] == ["one two three,", "four five six seven", "eight"]
    assert cues[0].start == 0.0 and cues[0].end == 0.9


def test_ass_output_scales_for_speed():
    ass = cues_to_ass([Cue(1.0, 2.0, "Hi {there}")], "bold", speed=2.0)
    assert "Dialogue: 0,0:00:00.50,0:00:01.00,Default,,0,0,0,,Hi (there)" in ass
    assert "PlayResY: 1920" in ass
    with pytest.raises(MediaError):
        cues_to_ass([], "fancy")


@needs_ffmpeg
def test_auto_subtitles_are_cached_and_burned(media_dir, tmp_path):
    _, master = normalize_video(probe(media_dir / "landscape.mp4"), tmp_path / "m.mp4")
    calls = []

    def fake(path, model, language):
        calls.append((model, language))
        return [(0.2, 0.6, "Morning"), (0.6, 1.0, "routine"), (1.0, 1.5, "works.")]

    cues = cues_for(master, "auto", tmp_path, "tiny", "en", transcriber=fake)
    assert [c.text for c in cues] == ["Morning routine works."]
    assert (tmp_path / "subtitles.srt").is_file()
    cues_for(master, "auto", tmp_path, "tiny", "en", transcriber=fake)
    assert len(calls) == 1  # second call reuses the edited/cached file
    out = burn_subtitles(master, tmp_path / "subbed.mp4", cues)
    assert (out.width, out.height) == (1080, 1920) and out.duration == pytest.approx(master.duration, abs=0.1)
    assert (tmp_path / "subbed.ass").is_file()


# ---------------------------------------------------------------------- auth


def test_token_store_is_private(settings):
    store = auth.store_for(settings)
    store.set("x", a=1)
    store.set("x", b=2)
    assert store.get("x") == {"a": 1, "b": 2}
    assert oct(settings.tokens_path.stat().st_mode)[-3:] == "600"


def test_instagram_login_token_refreshes_when_old(settings):
    settings.meta_graph_host = "graph.instagram.com"
    session = FakeSession({("GET", "refresh_access_token"): FakeResponse(data={"access_token": "new", "expires_in": 5184000})})
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    assert auth.instagram_token(settings, session, now) == "new"
    assert auth.store_for(settings).get("instagram")["access_token"] == "new"
    # refreshed recently: no second call
    assert auth.instagram_token(settings, session, now + timedelta(days=1)) == "new"
    assert len(session.calls) == 1
    # a week later it refreshes again
    auth.instagram_token(settings, session, now + timedelta(days=8))
    assert len(session.calls) == 2


def test_refresh_failure_keeps_working_token(settings):
    settings.meta_graph_host = "graph.instagram.com"
    session = FakeSession({("GET", "refresh_access_token"): FakeResponse(400, {"error": {"message": "too new"}})})
    assert auth.instagram_token(settings, session) == "igtoken"


def test_facebook_login_tokens_are_not_refreshed(settings):
    session = FakeSession({})
    assert auth.instagram_token(settings, session) == "igtoken"
    assert session.calls == []


def test_meta_setup_lists_pages(settings):
    settings.meta_app_id, settings.meta_app_secret = "app", "secret"
    session = FakeSession({
        ("GET", "oauth/access_token"): FakeResponse(data={"access_token": "long"}),
        ("GET", "me/accounts"): FakeResponse(data={"data": [{"id": "p1", "name": "My Page", "access_token": "ptok",
                                                             "instagram_business_account": {"id": "ig1", "username": "me"}}]}),
    })
    info = auth.meta_setup(settings, "short", session)
    assert info["exchanged"] and info["long_lived_user_token"] == "long"
    assert info["pages"][0] == {"page_id": "p1", "page_name": "My Page", "page_token": "ptok",
                                "ig_user_id": "ig1", "ig_username": "me"}


def tiktok_settings(settings):
    settings.tiktok_client_key, settings.tiktok_client_secret = "ck", "cs"
    settings.tiktok_redirect_uri = "https://example.com/cb"
    return settings


def test_tiktok_oauth_flow(settings):
    s = tiktok_settings(settings)
    url = auth.tiktok_authorize_url(s, state="abc")
    assert "client_key=ck" in url and "scope=user.info.basic%2Cvideo.upload" in url and "state=abc" in url
    session = FakeSession({("POST", "oauth/token"): FakeResponse(data={
        "access_token": "at", "refresh_token": "rt", "open_id": "o", "expires_in": 86400, "refresh_expires_in": 31536000})})
    with pytest.raises(PublishError, match="state"):
        auth.tiktok_exchange(s, "https://example.com/cb?code=C&state=wrong", session)
    record = auth.tiktok_exchange(s, "https://example.com/cb?code=C&state=abc", session)
    assert record["access_token"] == "at"
    assert session.calls[-1][2]["data"]["code"] == "C"


def test_tiktok_token_refreshes_after_expiry(settings):
    s = tiktok_settings(settings)
    auth.store_for(s).set("tiktok", access_token="old", refresh_token="rt",
                          expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    session = FakeSession({("POST", "oauth/token"): FakeResponse(data={"access_token": "fresh", "refresh_token": "rt2"})})
    assert auth.tiktok_token(s, session) == "fresh"
    assert session.calls[0][2]["data"]["grant_type"] == "refresh_token"


def test_tiktok_not_connected(settings):
    with pytest.raises(ConfigError, match="vauto auth tiktok"):
        auth.tiktok_token(settings)
