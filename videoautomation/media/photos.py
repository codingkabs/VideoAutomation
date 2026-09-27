"""Photo normalization and photo-set → slideshow video."""

from __future__ import annotations

from pathlib import Path

from ..errors import MediaError
from ..ffmpeg import probe, run_ffmpeg
from ..models import MediaInfo
from .normalize import AUDIO_ARGS, CONTAINER_ARGS, TARGET_H, TARGET_W, fit_graph, resolve_fit, video_codec_args

MAX_LONG_EDGE = 2048


def normalize_image(
    src: MediaInfo,
    out: Path,
    size: tuple[int, int] | None,
    fit: str = "auto",
    max_bytes: int | None = None,
) -> Path:
    """Write a high-quality JPEG. With ``size`` the image is fitted to that frame,
    otherwise it keeps its shape with the long edge capped. With ``max_bytes`` the
    JPEG quality, then the dimensions, are reduced until the file fits."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if size:
        w, h = size
    else:
        scale = min(1.0, MAX_LONG_EDGE / max(src.width, src.height))
        w = max(2, int(src.width * scale) // 2 * 2)
        h = max(2, int(src.height * scale) // 2 * 2)

    quality, shrink = 2, 1.0
    while True:
        tw, th = max(2, int(w * shrink) // 2 * 2), max(2, int(h * shrink) // 2 * 2)
        if size:
            mode = resolve_fit(fit, src.aspect, w / h)
            graph = fit_graph("0:v", "img", mode, tw, th) + ";[img]format=yuvj420p[v]"
        else:
            graph = f"[0:v]scale={tw}:{th},setsar=1,format=yuvj420p[v]"
        run_ffmpeg(["-i", src.path, "-filter_complex", graph, "-map", "[v]", "-frames:v", "1",
                    "-q:v", str(quality), str(out)])
        if not max_bytes or out.stat().st_size <= max_bytes:
            return out
        if quality < 10:
            quality += 3
        elif shrink > 0.3:
            shrink *= 0.8
        else:
            raise MediaError(f"Could not get {Path(src.path).name} under {max_bytes // 1024} KB")


def make_slideshow(
    images: list[MediaInfo],
    out: Path,
    seconds_per_image: float = 3.0,
    audio: Path | None = None,
    fit: str = "auto",
    fps: int = 30,
) -> tuple[Path, MediaInfo]:
    """Build a 1080x1920 video from a photo set, for video-only platforms."""
    if not images:
        raise MediaError("No images for slideshow")
    out.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    parts: list[str] = []
    for i, img in enumerate(images):
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds_per_image:.3f}", "-i", img.path]
        mode = resolve_fit(fit, img.aspect, TARGET_W / TARGET_H)
        parts.append(fit_graph(f"{i}:v", f"s{i}", mode, TARGET_W, TARGET_H, prefix=f"p{i}"))
        parts.append(f"[s{i}]fps={fps},format=yuv420p[c{i}]")
    concat_inputs = "".join(f"[c{i}]" for i in range(len(images)))
    parts.append(f"{concat_inputs}concat=n={len(images)}:v=1:a=0[v]")
    total = seconds_per_image * len(images)

    audio_index = len(images)
    if audio is not None:
        if not Path(audio).is_file():
            raise MediaError(f"Audio file not found: {audio}")
        args += ["-stream_loop", "-1", "-i", str(audio)]
        fade_start = max(0.0, total - 1.0)
        parts.append(f"[{audio_index}:a]afade=t=out:st={fade_start:.3f}:d=1[a]")
        audio_map = "[a]"
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        audio_map = f"{audio_index}:a:0"

    args += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", audio_map]
    args += video_codec_args() + AUDIO_ARGS + CONTAINER_ARGS + ["-t", f"{total:.3f}", str(out)]
    run_ffmpeg(args)
    return out, probe(out)
