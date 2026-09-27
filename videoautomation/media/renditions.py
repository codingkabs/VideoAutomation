"""Per-platform video renditions derived from the master."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import MediaError
from ..ffmpeg import probe
from ..models import MediaInfo
from .normalize import encode

AUDIO_KBPS = 128
SIZE_SAFETY = 0.92  # stay under the cap after container overhead


@dataclass
class RenditionPlan:
    duration: float
    video_bitrate_k: int | None
    notes: list[str] = field(default_factory=list)

    @property
    def is_master(self) -> bool:
        return self.video_bitrate_k is None and not self.notes

    def filename(self) -> str:
        parts = [f"t{self.duration:.2f}"]
        if self.video_bitrate_k:
            parts.append(f"b{self.video_bitrate_k}")
        return "rendition_" + "_".join(parts) + ".mp4"


def plan_rendition(master: MediaInfo, spec: dict[str, Any], platform_name: str, allow_trim: bool) -> RenditionPlan:
    """Decide whether the master fits the platform, needs a trim, or a smaller file.

    Raises MediaError when the video cannot be made to fit.
    """
    min_s = float(spec.get("min_s") or 0)
    max_s = float(spec.get("max_s") or 0)
    max_mb = float(spec.get("max_mb") or 0)
    duration = master.duration
    notes: list[str] = []

    if min_s and duration + 0.05 < min_s:
        raise MediaError(f"{platform_name} needs at least {min_s:g}s; this video is {duration:.1f}s")

    if max_s and duration > max_s + 0.05:
        if not allow_trim:
            raise MediaError(
                f"{platform_name} allows up to {max_s:g}s; this video is {duration:.1f}s "
                f"(pass --trim to cut it to {max_s:g}s)"
            )
        notes.append(f"trimmed from {duration:.1f}s to {max_s:g}s")
        duration = max_s

    bitrate = None
    if max_mb:
        cap_bytes = max_mb * 1024 * 1024 * SIZE_SAFETY
        estimated = master.size_bytes * (duration / master.duration) if master.duration else master.size_bytes
        if estimated > cap_bytes:
            total_kbps = cap_bytes * 8 / 1000 / duration
            bitrate = int(total_kbps - AUDIO_KBPS)
            if bitrate < 300:
                raise MediaError(f"{platform_name} caps files at {max_mb:g} MB, too small for {duration:.0f}s of video")
            notes.append(f"re-encoded at {bitrate} kbps to fit {max_mb:g} MB")

    return RenditionPlan(duration=duration if notes else master.duration, video_bitrate_k=bitrate, notes=notes)


def make_rendition(master: MediaInfo, plan: RenditionPlan, out_dir: Path) -> MediaInfo:
    """Return the master itself when it already fits, otherwise encode (cached by plan)."""
    if plan.is_master:
        return master
    out = out_dir / plan.filename()
    if out.is_file():
        return probe(out)
    encode(master, out, "[0:v]null[v]", duration=plan.duration, video_bitrate_k=plan.video_bitrate_k)
    return probe(out)
