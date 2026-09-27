import json
from datetime import timedelta

import pytest

from videoautomation.errors import ConfigError, PublishError
from videoautomation.models import MediaFile, PostJob
from videoautomation.publishers.meta import FacebookPublisher, InstagramPublisher
from videoautomation.publishers.zernio import ZernioPublisher
from videoautomation.scheduler import iso, utcnow

from .conftest import FakeResponse, FakeSession


@pytest.fixture
def video(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00" * 2048)
    return MediaFile(str(path), "video")


class StubStorage:
    name = "stub"

    def __init__(self):
        self.puts = []

    def put(self, path, key):
        self.puts.append(key)
        return f"https://cdn.example/{key}"


def no_sleep(_):
    pass


# ------------------------------------------------------------------ Instagram


def test_instagram_trial_reel_flow(settings, video):
    session = FakeSession({
        ("GET", "content_publishing_limit"): FakeResponse(data={"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]}),
        ("POST", "ig123/media_publish"): FakeResponse(data={"id": "media_9"}),
        ("POST", "ig123/media"): FakeResponse(data={"id": "cont_1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/cont_1"}),
        ("POST", "rupload.facebook.com"): FakeResponse(data={"success": True}),
        ("GET", "cont_1"): [FakeResponse(data={"status_code": "IN_PROGRESS"}), FakeResponse(data={"status_code": "FINISHED"})],
        ("GET", "media_9"): FakeResponse(data={"permalink": "https://www.instagram.com/reel/abc/"}),
    })
    pub = InstagramPublisher(settings, session=session, sleep=no_sleep)
    job = PostJob("instagram", "trial_reel", "meta", [video], "caption #a",
                  options={"trial_graduation": "MANUAL", "thumb_offset_ms": 1500})
    result = pub.publish(job)

    assert result.status == "published"
    assert result.url == "https://www.instagram.com/reel/abc/"
    container_call = next(c for c in session.calls if c[0] == "POST" and c[1].endswith("ig123/media"))
    data = container_call[2]["data"]
    assert data["media_type"] == "REELS" and data["upload_type"] == "resumable"
    assert json.loads(data["trial_params"]) == {"graduation_strategy": "MANUAL"}
    assert data["thumb_offset"] == 1500
    assert "share_to_feed" not in data
    upload = next(c for c in session.calls if "rupload" in c[1])
    assert upload[2]["headers"]["Authorization"] == "OAuth igtoken"
    assert upload[2]["headers"]["file_size"] == "2048"


def test_instagram_regular_reel_shares_to_feed(settings, video):
    session = FakeSession({
        ("GET", "content_publishing_limit"): FakeResponse(data={"data": []}),
        ("POST", "ig123/media_publish"): FakeResponse(data={"id": "m"}),
        ("POST", "ig123/media"): FakeResponse(data={"id": "c"}),
        ("POST", "rupload"): FakeResponse(data={"success": True}),
        ("GET", "/c"): FakeResponse(data={"status_code": "FINISHED"}),
        ("GET", "/m"): FakeResponse(data={"permalink": "https://instagram.com/p/x"}),
    })
    InstagramPublisher(settings, session=session, sleep=no_sleep).publish(
        PostJob("instagram", "reel", "meta", [video], "hi"))
    data = next(c for c in session.calls if c[1].endswith("ig123/media"))[2]["data"]
    assert data["share_to_feed"] == "true" and "trial_params" not in data


def test_instagram_container_error_is_reported(settings, video):
    session = FakeSession({
        ("GET", "content_publishing_limit"): FakeResponse(data={"data": []}),
        ("POST", "ig123/media"): FakeResponse(data={"id": "c"}),
        ("POST", "rupload"): FakeResponse(data={"success": True}),
        ("GET", "/c"): FakeResponse(data={"status_code": "ERROR", "status": "Unsupported audio codec"}),
    })
    with pytest.raises(PublishError, match="Unsupported audio codec"):
        InstagramPublisher(settings, session=session, sleep=no_sleep).publish(
            PostJob("instagram", "reel", "meta", [video], "hi"))


def test_instagram_quota_exhausted(settings, video):
    session = FakeSession({
        ("GET", "content_publishing_limit"): FakeResponse(data={"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]}),
    })
    with pytest.raises(PublishError, match="limit reached"):
        InstagramPublisher(settings, session=session).publish(PostJob("instagram", "reel", "meta", [video], "hi"))


def test_instagram_carousel_uploads_images(settings, tmp_path):
    imgs = []
    for i in range(3):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"jpg")
        imgs.append(MediaFile(str(p), "image"))
    storage = StubStorage()
    created = iter(["c0", "c1", "c2", "parent"])
    session = FakeSession({
        ("GET", "content_publishing_limit"): FakeResponse(data={"data": []}),
        ("POST", "ig123/media_publish"): FakeResponse(data={"id": "m"}),
        ("POST", "ig123/media"): lambda url, kw: FakeResponse(data={"id": next(created)}),
        ("GET", "graph.facebook.com/v23.0/m"): FakeResponse(data={"permalink": "https://instagram.com/p/car"}),
        ("GET", "graph.facebook.com/v23.0/"): FakeResponse(data={"status_code": "FINISHED"}),
    })
    job = PostJob("instagram", "carousel", "meta", imgs, "three pics", post_id="p1")
    result = InstagramPublisher(settings, storage, session=session, sleep=no_sleep).publish(job)
    assert result.status == "published"
    assert storage.puts == ["p1/0.jpg", "p1/1.jpg", "p1/2.jpg"]
    parent = [c for c in session.calls if c[1].endswith("ig123/media")][-1][2]["data"]
    assert parent["media_type"] == "CAROUSEL" and parent["children"] == "c0,c1,c2"


def test_instagram_photo_without_storage_explains(settings, tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    session = FakeSession({("GET", "content_publishing_limit"): FakeResponse(data={"data": []})})
    with pytest.raises(ConfigError, match="public URL"):
        InstagramPublisher(settings, None, session=session).publish(
            PostJob("instagram", "image", "meta", [MediaFile(str(p), "image")], "x"))


# ------------------------------------------------------------------- Facebook


def test_facebook_reel_three_phases(settings, video):
    session = FakeSession({
        ("POST", "page1/video_reels"): [
            FakeResponse(data={"video_id": "v1", "upload_url": "https://rupload.facebook.com/video-upload/v23.0/v1"}),
            FakeResponse(data={"success": True}),
        ],
        ("POST", "rupload.facebook.com"): FakeResponse(data={"success": True}),
        ("GET", "v1"): [
            FakeResponse(data={"status": {"video_status": "processing"}}),
            FakeResponse(data={"status": {"video_status": "ready", "publishing_phase": {"status": "complete"}}}),
            FakeResponse(data={"permalink_url": "/reel/555"}),
        ],
    })
    result = FacebookPublisher(settings, session=session, sleep=no_sleep).publish(
        PostJob("facebook", "reel", "meta", [video], "fb caption"))
    assert result.status == "published"
    assert result.url == "https://www.facebook.com/reel/555"
    finish = [c for c in session.calls if c[1].endswith("page1/video_reels")][1][2]["data"]
    assert finish["upload_phase"] == "finish" and finish["video_state"] == "PUBLISHED"
    assert finish["description"] == "fb caption"


def test_meta_http_errors_classified(settings, video):
    session = FakeSession({
        ("POST", "page1/video_reels"): FakeResponse(500, {"error": {"message": "boom", "code": 2}}),
    })
    with pytest.raises(PublishError) as err:
        FacebookPublisher(settings, session=session).publish(PostJob("facebook", "reel", "meta", [video], "x"))
    assert err.value.transient and "boom" in str(err.value)

    session = FakeSession({
        ("POST", "page1/video_reels"): FakeResponse(400, {"error": {"message": "bad token", "code": 190}}),
    })
    with pytest.raises(PublishError) as err:
        FacebookPublisher(settings, session=session).publish(PostJob("facebook", "reel", "meta", [video], "x"))
    assert not err.value.transient


# --------------------------------------------------------------------- Zernio


def zernio_session(post_response, status_response=None, creator_info=None):
    routes = {
        ("GET", "tiktok/creator-info"): FakeResponse(data=creator_info or {"privacyLevelOptions": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"], "maxVideoPostDurationSec": 600}),
        ("POST", "/posts"): FakeResponse(data=post_response),
    }
    if status_response is not None:
        routes[("GET", "/posts/")] = FakeResponse(data=status_response)
    return FakeSession(routes)


def test_zernio_tiktok_draft_body(settings, video):
    video.url = "https://cdn.example/clip.mp4"
    session = zernio_session({"post": {"_id": "p1", "status": "published",
                                       "platforms": [{"platform": "tiktok", "status": "published"}]}})
    job = PostJob("tiktok", "video", "zernio", [video], "tt caption", options={"draft": True, "duration_s": 20})
    result = ZernioPublisher(settings, session=session, sleep=no_sleep).publish(job)
    assert result.status == "draft"
    body = next(c for c in session.calls if c[0] == "POST")[2]["json"]
    assert body["publishNow"] is True
    assert body["mediaItems"] == [{"type": "video", "url": "https://cdn.example/clip.mp4"}]
    psd = body["platforms"][0]["platformSpecificData"]
    assert body["platforms"][0]["accountId"] == "acc_tt"
    assert psd["draft"] is True and psd["contentPreviewConfirmed"] and psd["expressConsentGiven"]
    assert psd["allowDuet"] is True and psd["allowStitch"] is True


def test_zernio_tiktok_rejects_long_video(settings, video):
    video.url = "https://cdn.example/clip.mp4"
    session = zernio_session({}, creator_info={"maxVideoPostDurationSec": 60})
    job = PostJob("tiktok", "video", "zernio", [video], "x", options={"duration_s": 120})
    with pytest.raises(PublishError, match="60"):
        ZernioPublisher(settings, session=session).publish(job)


def test_zernio_youtube_polls_until_published(settings, video):
    video.url = "https://cdn.example/clip.mp4"
    session = zernio_session(
        {"post": {"_id": "p2", "status": "publishing", "platforms": [{"platform": "youtube", "status": "publishing"}]}},
        status_response={"post": {"_id": "p2", "platforms": [{"platform": "youtube", "status": "published",
                                                                "publishedUrl": "https://youtube.com/shorts/xyz"}]}},
    )
    job = PostJob("youtube", "short", "zernio", [video], "desc",
                  options={"title": "My title #Shorts", "tags": ["a", "b"]})
    result = ZernioPublisher(settings, session=session, sleep=no_sleep).publish(job)
    assert result.status == "published" and result.url == "https://youtube.com/shorts/xyz"
    body = next(c for c in session.calls if c[0] == "POST")[2]["json"]
    assert body["platforms"][0]["platformSpecificData"] == {"title": "My title #Shorts", "visibility": "public"}
    assert body["tags"] == ["a", "b"]


def test_zernio_failure_raises(settings, video):
    video.url = "u"
    session = zernio_session({"post": {"_id": "p", "platforms": [{"platform": "snapchat", "status": "failed",
                                                                   "error": "Video too short"}]}})
    with pytest.raises(PublishError, match="Video too short"):
        ZernioPublisher(settings, session=session).publish(PostJob("snapchat", "spotlight", "zernio", [video], "x"))


def test_zernio_future_run_at_schedules_remotely(settings, video):
    video.url = "u"
    session = zernio_session({"post": {"_id": "p3", "status": "scheduled"}})
    run_at = iso(utcnow() + timedelta(minutes=90))
    job = PostJob("instagram", "trial_reel", "zernio", [video], "x", run_at=run_at,
                  options={"trial_graduation": "SS_PERFORMANCE"})
    result = ZernioPublisher(settings, session=session).publish(job)
    assert result.status == "scheduled" and result.run_at == run_at
    body = next(c for c in session.calls if c[0] == "POST")[2]["json"]
    assert body["scheduledFor"] == run_at and "publishNow" not in body
    assert body["platforms"][0]["platformSpecificData"]["trialParams"] == {"graduationStrategy": "SS_PERFORMANCE"}


def test_zernio_missing_account(settings, video):
    settings.zernio_accounts.pop("snapchat")
    video.url = "u"
    with pytest.raises(ConfigError, match="ZERNIO_ACCOUNT_SNAPCHAT"):
        ZernioPublisher(settings, session=FakeSession({})).publish(
            PostJob("snapchat", "spotlight", "zernio", [video], "x"))


def test_backend_override_validated(tmp_path):
    from videoautomation.config import Settings
    with pytest.raises(ConfigError, match="VAUTO_BACKEND_TIKTOK"):
        Settings.from_env(env={"VAUTO_HOME": str(tmp_path), "VAUTO_BACKEND_TIKTOK": "meta"}, dotenv=None)
