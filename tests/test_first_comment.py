"""First comment: your own text or Claude's, on the platforms that let apps comment."""

from types import SimpleNamespace

import pytest

from videoautomation import captions, pipeline, service
from videoautomation.config import Settings
from videoautomation.errors import ConfigError, VautoError
from videoautomation.models import MediaFile, PostJob, PostResult
from videoautomation.pipeline import PostRequest, build_plan
from videoautomation.publishers.meta import InstagramPublisher
from videoautomation.publishers.zernio import ZernioPublisher

from .conftest import FakeResponse, FakeSession, needs_ffmpeg

PLATFORMS = ["instagram", "facebook", "tiktok", "youtube", "linkedin"]


def _plan(settings, media_dir, **kw):
    base = dict(inputs=[media_dir / "vertical_silent.mp4"], caption="Sunset run #uk", platforms=PLATFORMS,
                trial=True)
    base.update(kw)
    return build_plan(PostRequest(**base), settings)


def _comments(plan):
    return {(j.platform, j.surface): j.options.get("first_comment") for j in plan.jobs}


@needs_ffmpeg
def test_your_own_comment_goes_where_apps_can_comment(settings, media_dir):
    plan = _plan(settings, media_dir, first_comment_mode="mine", first_comment="Which view was best? 👇")
    got = _comments(plan)
    for key in [("instagram", "reel"), ("facebook", "reel"), ("youtube", "short"), ("linkedin", "video"),
                ("instagram", "trial_reel")]:
        assert got[key] == "Which view was best? 👇", key
    assert got[("tiktok", "video")] is None and got[("instagram", "trial_report")] is None
    assert plan.first_comment == "Which view was best? 👇"
    assert plan.notes[0].startswith("first comment on Instagram, Facebook Page, YouTube Shorts, LinkedIn")
    assert any("no first comment on TikTok" in n for n in plan.notes)


@needs_ffmpeg
def test_off_and_empty(settings, media_dir):
    assert not any(_comments(_plan(settings, media_dir)).values())
    plan = _plan(settings, media_dir, first_comment_mode="mine", first_comment="  ")
    assert not any(_comments(plan).values()) and any("type the comment first" in n for n in plan.notes)


@needs_ffmpeg
def test_claude_writes_it_from_video_frames(settings, media_dir, monkeypatch):
    seen = {}

    def fake(caption, frames, model, api_key):
        seen.update(caption=caption, frames=frames, model=model, key=api_key)
        return "Did you catch the ending? 👀"

    monkeypatch.setattr(captions, "first_comment", fake)
    settings.anthropic_api_key = "sk-ant-test"
    plan = _plan(settings, media_dir, first_comment_mode="claude")
    assert plan.first_comment == "Did you catch the ending? 👀"
    assert _comments(plan)[("instagram", "reel")] == "Did you catch the ending? 👀"
    assert len(seen["frames"]) == 4 and all(f.is_file() and f.suffix == ".jpg" for f in seen["frames"])
    assert seen["caption"].startswith("Sunset run") and seen["key"] == "sk-ant-test"
    assert plan.notes[0].startswith("Claude's first comment on")

    # Text you edited after Preview is used as it is; Claude is not asked again.
    monkeypatch.setattr(captions, "first_comment", lambda *a: pytest.fail("should not call Claude"))
    plan = _plan(settings, media_dir, first_comment_mode="claude", first_comment="My edit")
    assert plan.first_comment == "My edit"


@needs_ffmpeg
def test_claude_without_a_key_skips_with_a_clear_note(settings, media_dir):
    settings.anthropic_api_key = None
    plan = _plan(settings, media_dir, first_comment_mode="claude")
    assert plan.first_comment == "" and any("add an Anthropic API key" in n for n in plan.notes)


def test_zernio_sends_first_comment(settings):
    pub = ZernioPublisher(settings)
    job = PostJob("instagram", "reel", "zernio", [MediaFile("x.mp4", "video", url="https://cdn/x.mp4")], "cap",
                  options={"first_comment": "Hi 👋", "trial_graduation": None})
    body = pub.build_body(job)
    assert body["platforms"][0]["platformSpecificData"]["firstComment"] == "Hi 👋"
    post = {"platforms": [{"platform": "instagram", "status": "published", "publishedUrl": "https://ig/p"}]}
    assert "first comment added" in pub._await(job, "zp", post, []).notes


def test_meta_direct_comment_is_best_effort(settings):
    job = PostJob("instagram", "reel", "meta", [], "c", options={"first_comment": "Nice"})
    ok = FakeSession({("POST", "/m1/comments"): FakeResponse(data={"id": "c1"})})
    notes: list[str] = []
    InstagramPublisher(settings, session=ok)._first_comment("m1", "tok", job, notes)
    assert notes == ["first comment added"] and ok.calls[0][2]["data"]["message"] == "Nice"
    bad = FakeSession({("POST", "/m1/comments"): FakeResponse(400, {"error": {"message": "no permission"}})})
    notes = []
    InstagramPublisher(settings, session=bad)._first_comment("m1", "tok", job, notes)
    assert notes and notes[0].startswith("first comment not added")


class _FakeAnthropic:
    last: dict = {}

    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        pass

    def __init__(self, api_key=None):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.reply = '{"comment": "Wait for the last second 😂"}'
        self.stop = "end_turn"

    def _create(self, **kwargs):
        _FakeAnthropic.last = kwargs
        return SimpleNamespace(stop_reason=_FakeAnthropic.stop, content=[SimpleNamespace(type="text",
                                                                                          text=_FakeAnthropic.reply)])


def test_claude_request_sends_frames_and_reads_the_comment(monkeypatch, tmp_path):
    import sys

    fake_module = SimpleNamespace(Anthropic=_FakeAnthropic, APIConnectionError=_FakeAnthropic.APIConnectionError,
                                  APIStatusError=_FakeAnthropic.APIStatusError)
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    frames = []
    for i in range(4):
        f = tmp_path / f"f{i}.jpg"
        f.write_bytes(b"\xff\xd8jpeg")
        frames.append(f)
    _FakeAnthropic.reply, _FakeAnthropic.stop = '{"comment": "Wait for the last second 😂"}', "end_turn"
    assert captions.first_comment("Gym day #gym", frames, "claude-opus-5", "k") == "Wait for the last second 😂"
    sent = _FakeAnthropic.last
    blocks = sent["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image"] * 4 + ["text"] and "Gym day" in blocks[-1]["text"]
    assert sent["model"] == "claude-opus-5" and sent["fallbacks"] == "default"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    _FakeAnthropic.stop = "refusal"
    with pytest.raises(VautoError, match="declined"):
        captions.first_comment("x", frames, "claude-opus-5", "k")
    _FakeAnthropic.stop, _FakeAnthropic.reply = "end_turn", '{"comment": ""}'
    with pytest.raises(VautoError, match="empty"):
        captions.first_comment("x", frames, "claude-opus-5", "k")


def test_defaults_come_from_settings(settings, media_dir):
    s = Settings.from_env(env={"VAUTO_HOME": str(settings.home), "VAUTO_FIRST_COMMENT": "mine",
                               "VAUTO_FIRST_COMMENT_TEXT": "Follow for more 🙌"}, dotenv=None)
    req = service.make_request(s, [media_dir / "vertical_silent.mp4"], "c", ["instagram"])
    assert req.first_comment_mode == "mine" and req.first_comment == "Follow for more 🙌"
    req = service.make_request(s, [media_dir / "vertical_silent.mp4"], "c", ["instagram"], first_comment_mode="off")
    assert req.first_comment_mode == "off"
    with pytest.raises(ConfigError, match="VAUTO_FIRST_COMMENT"):
        Settings.from_env(env={"VAUTO_HOME": "/tmp/x", "VAUTO_FIRST_COMMENT": "sometimes"}, dotenv=None)


@needs_ffmpeg
def test_cli_first_comment(monkeypatch, capsys, settings, media_dir):
    from videoautomation import cli

    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, *a, **k: settings))
    code = cli.main(["post", str(media_dir / "vertical_silent.mp4"), "-c", "hi", "-p", "instagram,tiktok",
                     "--first-comment", "Thoughts? 👇", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and "first comment on Instagram: “Thoughts? 👇”" in out and "no first comment on TikTok" in out


def test_web_exposes_the_comment_options(settings, tmp_path):
    pytest.importorskip("flask")
    from videoautomation.web.app import create_app

    settings.dotenv_path = tmp_path / ".env"
    settings.first_comment_mode, settings.first_comment_text = "mine", "Hi!"
    client = create_app(settings).test_client()
    defaults = client.get("/api/status").get_json()["defaults"]
    assert defaults["first_comment_mode"] == "mine" and defaults["first_comment_text"] == "Hi!"
    html = client.get("/").get_data(as_text=True)
    assert 'id="opt-comment"' in html and 'id="opt-comment-text"' in html
