"""Still frames from a video, for Claude to look at when it writes a first comment."""

from __future__ import annotations

from pathlib import Path

from ..errors import MediaError
from ..ffmpeg import run_ffmpeg
from ..models import MediaInfo

FRAME_WIDTH = 512  # small: enough to see what the video is about, cheap to send


def sample_frames(video: MediaInfo, out_dir: Path, count: int = 4) -> list[Path]:
    """``count`` JPEG frames spread through the video (skipping the very start and end)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    duration = max(video.duration, 0.1)
    for i in range(count):
        t = duration * (i + 0.5) / count
        out = out_dir / f"frame_{i:02d}.jpg"
        if not out.is_file():
            try:
                run_ffmpeg(["-ss", f"{t:.2f}", "-i", video.path, "-frames:v", "1",
                            "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "4", str(out)])
            except MediaError:
                continue
        if out.is_file() and out.stat().st_size:
            frames.append(out)
    return frames
