import pytest

from videoautomation.errors import MediaError
from videoautomation.ffmpeg import probe
from videoautomation.media.normalize import normalize_video, resolve_fit
from videoautomation.media.photos import make_slideshow, normalize_image
from videoautomation.media.renditions import make_rendition, plan_rendition
from videoautomation.media.variants import VariantOptions, render_variant

from .conftest import needs_ffmpeg

pytestmark = needs_ffmpeg


def test_resolve_fit_auto():
    assert resolve_fit("auto", 9 / 16, 9 / 16) == "crop"
    assert resolve_fit("auto", 16 / 9, 9 / 16) == "blur"
    assert resolve_fit("pad", 16 / 9, 9 / 16) == "pad"
    with pytest.raises(MediaError):
        resolve_fit("stretch", 1, 1)


@pytest.fixture(scope="module")
def master(media_dir, tmp_path_factory):
    out = tmp_path_factory.mktemp("render") / "master.mp4"
    _, info = normalize_video(probe(media_dir / "landscape.mp4"), out)
    return info


def test_probe_reads_video(media_dir):
    info = probe(media_dir / "landscape.mp4")
    assert (info.width, info.height) == (640, 360)
    assert info.has_audio and not info.is_image
    assert info.duration == pytest.approx(6.0, abs=0.1)


def test_normalize_is_vertical_h264_aac(master):
    assert (master.width, master.height) == (1080, 1920)
    assert master.vcodec == "h264" and master.acodec == "aac"
    assert master.duration == pytest.approx(6.0, abs=0.1)


def test_silent_input_gets_audio_track(media_dir, tmp_path):
    _, info = normalize_video(probe(media_dir / "vertical_silent.mp4"), tmp_path / "m.mp4", "pad")
    assert info.has_audio
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(4.0, abs=0.1)


def test_rendition_uses_master_when_it_fits(master, tmp_path):
    plan = plan_rendition(master, {"min_s": 3, "max_s": 90, "max_mb": 100}, "Facebook", False)
    assert plan.is_master
    assert make_rendition(master, plan, tmp_path).path == master.path


def test_rendition_rejects_too_long_without_trim(master):
    with pytest.raises(MediaError, match="--trim"):
        plan_rendition(master, {"max_s": 4}, "Test", False)


def test_rendition_trims_when_allowed(master, tmp_path):
    plan = plan_rendition(master, {"max_s": 4}, "Test", True)
    out = make_rendition(master, plan, tmp_path)
    assert out.duration == pytest.approx(4.0, abs=0.1)


def test_rendition_rejects_too_short(master):
    with pytest.raises(MediaError, match="at least"):
        plan_rendition(master, {"min_s": 10}, "Snapchat", True)


def test_rendition_shrinks_to_size_cap(master, tmp_path):
    cap_mb = master.size_bytes / 1024 / 1024 / 2
    plan = plan_rendition(master, {"max_mb": cap_mb}, "Test", False)
    assert plan.video_bitrate_k
    out = make_rendition(master, plan, tmp_path)
    assert out.size_bytes <= cap_mb * 1024 * 1024


@pytest.mark.parametrize("opts", [
    VariantOptions(mode="static", zoom=1.2),
    VariantOptions(mode="push", zoom=1.15, mirror=True),
    VariantOptions(mode="static", hook_text="Wait for it", speed=1.05),
])
def test_variant_renders_vertical(master, tmp_path, opts):
    info = render_variant(master, tmp_path / "v.mp4", opts)
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(master.duration / opts.speed, abs=0.15)
    assert info.has_audio


def test_variant_validates_zoom():
    with pytest.raises(MediaError):
        VariantOptions(zoom=1.0).validate()


def test_normalize_image_sizes(media_dir, tmp_path):
    out = normalize_image(probe(media_dir / "b.jpg"), tmp_path / "ig.jpg", (1080, 1350))
    info = probe(out)
    assert (info.width, info.height) == (1080, 1350) and info.is_image
    keep = probe(normalize_image(probe(media_dir / "b.jpg"), tmp_path / "fb.jpg", None))
    assert (keep.width, keep.height) == (1600, 900)


def test_slideshow(media_dir, tmp_path):
    imgs = [probe(media_dir / "a.png"), probe(media_dir / "b.jpg")]
    _, info = make_slideshow(imgs, tmp_path / "s.mp4", 2.5, audio=media_dir / "landscape.mp4")
    assert (info.width, info.height) == (1080, 1920)
    assert info.duration == pytest.approx(5.0, abs=0.15)
    assert info.has_audio
