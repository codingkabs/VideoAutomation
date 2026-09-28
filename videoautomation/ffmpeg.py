"""Locate ffmpeg/ffprobe, run them, and probe media files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from .errors import MediaError
from .models import MediaInfo

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".3gp"}


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    candidate = os.environ.get("VAUTO_FFMPEG") or shutil.which("ffmpeg")
    if candidate:
        return candidate
    try:  # optional pip fallback: imageio-ffmpeg ships a static ffmpeg
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on the machine
        raise MediaError("ffmpeg not found. Install ffmpeg or set VAUTO_FFMPEG.") from exc


@lru_cache(maxsize=1)
def ffprobe_bin() -> str:
    candidate = os.environ.get("VAUTO_FFPROBE") or shutil.which("ffprobe")
    if not candidate:
        raise MediaError("ffprobe not found. Install ffmpeg (it includes ffprobe) or set VAUTO_FFPROBE.")
    return candidate


def run_ffmpeg(args: list[str], timeout: float | None = None) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"ffmpeg timed out after {timeout}s") from exc
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-8:])
        raise MediaError(f"ffmpeg failed (exit {proc.returncode}):\n{tail}")


def _fps(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def _rotation(stream: dict) -> int:
    rotate = stream.get("tags", {}).get("rotate")
    if rotate is not None:
        return int(float(rotate)) % 360
    for side in stream.get("side_data_list", []) or []:
        if "rotation" in side:
            return int(float(side["rotation"])) % 360
    return 0


def is_image_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


@lru_cache(maxsize=1)
def has_filter(name: str) -> bool:
    """True when this ffmpeg build has the filter (e.g. zscale for HDR tone-mapping)."""
    try:
        out = subprocess.run([ffmpeg_bin(), "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    except (OSError, MediaError):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def probe(path: str | Path) -> MediaInfo:
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"File not found: {path}")
    cmd = [
        ffprobe_bin(), "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path),
    ]
    if is_image_path(path):
        # Phone photos are often stored sideways with an EXIF rotation, which ffprobe
        # only reports on the decoded frame. ffmpeg itself rotates them when reading.
        cmd[-1:-1] = ["-show_frames", "-read_intervals", "%+#1"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise MediaError(f"Could not read {path.name}: {proc.stderr.strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"{path.name} has no video or image stream")

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    rotation = _rotation(video)
    if not rotation:
        frame = next((f for f in data.get("frames", []) if f.get("media_type", "video") == "video"), {})
        rotation = _rotation(frame)
    if rotation in (90, 270):
        width, height = height, width

    fmt = data.get("format", {})
    format_name = fmt.get("format_name", "")
    image = is_image_path(path) or "image2" in format_name or format_name.endswith("_pipe")
    duration = 0.0 if image else float(fmt.get("duration") or video.get("duration") or 0.0)

    return MediaInfo(
        path=str(path),
        width=width,
        height=height,
        duration=duration,
        fps=0.0 if image else _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        vcodec=video.get("codec_name", ""),
        acodec=audio.get("codec_name") if audio else None,
        size_bytes=int(fmt.get("size") or path.stat().st_size),
        is_image=image,
        color_transfer=str(video.get("color_transfer") or ""),
    )
