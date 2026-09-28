"""Trial-reel variant: a visibly different cut of the same video.

Instagram demotes near-duplicates, so the variant changes framing (static zoom
or a slow push-in), and can add a hook caption, a mirror flip, or a small speed
change. The pipeline also gives it a different cover frame.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from ..errors import MediaError
from ..ffmpeg import probe
from ..models import MediaInfo
from .normalize import TARGET_H, TARGET_W, encode

VARIANT_MODES = ("static", "push")
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


@dataclass
class VariantOptions:
    mode: str = "static"
    zoom: float = 1.15
    mirror: bool = False
    speed: float = 1.0
    hook_text: str | None = None
    hook_seconds: float = 3.0
    font_path: str | None = None

    def validate(self) -> None:
        if self.mode not in VARIANT_MODES:
            raise MediaError(f"Unknown trial mode {self.mode!r}; use static or push")
        if not 1.0 < self.zoom <= 2.0:
            raise MediaError("Trial zoom must be above 1.0 and at most 2.0 (for example 1.15)")
        if not 0.5 <= self.speed <= 2.0:
            raise MediaError("Trial speed must be between 0.5 and 2.0")


def find_font(preferred: str | None = None) -> str | None:
    if preferred:
        if Path(preferred).is_file():
            return preferred
        raise MediaError(f"Font not found: {preferred}")
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    if shutil.which("fc-match"):
        proc = subprocess.run(["fc-match", "-f", "%{file}", "sans:bold"], capture_output=True, text=True)
        if proc.returncode == 0 and Path(proc.stdout.strip()).is_file():
            return proc.stdout.strip()
    return None


def _escape_filter_path(path: str) -> str:
    """Escape a path for use as a quoted filtergraph option value."""
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _hook_filters(opts: VariantOptions, work_dir: Path) -> list[str]:
    """One centred drawtext per line (text read from files to avoid escaping issues)."""
    font = find_font(opts.font_path)
    if font is None:
        raise MediaError("No font found for hook text. Set VAUTO_FONT to a .ttf file.")
    lines = textwrap.wrap(opts.hook_text or "", width=20)[:4]
    font_size = 76
    line_height = int(font_size * 1.25)
    top = int(TARGET_H * 0.16)
    filters = []
    for i, line in enumerate(lines):
        text_file = work_dir / f"hook_line_{i}.txt"
        text_file.write_text(line, encoding="utf-8")
        filters.append(
            "drawtext="
            f"fontfile='{_escape_filter_path(font)}':"
            f"textfile='{_escape_filter_path(str(text_file))}':expansion=none:"
            f"fontsize={font_size}:fontcolor=white:borderw=6:bordercolor=black@0.85:"
            f"x=(w-text_w)/2:y={top + i * line_height}:"
            f"enable='lt(t,{opts.hook_seconds:g})'"
        )
    return filters


def variant_graph(master: MediaInfo, opts: VariantOptions, work_dir: Path) -> tuple[str, str | None]:
    """Return (video graph reading [0:v] → [v], audio filter or None)."""
    opts.validate()
    chain: list[str] = []
    if opts.mode == "static":
        chain.append(f"crop=iw/{opts.zoom:g}:ih/{opts.zoom:g}")
        chain.append(f"scale={TARGET_W}:{TARGET_H}")
    else:
        fps = master.fps if master.fps > 0 else 30.0
        frames = max(1, math.ceil(master.duration * fps))
        # Upscale first so zoompan's integer crop steps are too small to see.
        chain.append(f"scale={TARGET_W * 2}:{TARGET_H * 2}")
        chain.append(
            f"zoompan=z='min(1+{opts.zoom - 1:g}*on/{frames},{opts.zoom:g})':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={TARGET_W}x{TARGET_H}:fps={fps:g}"
        )
    chain.append("setsar=1")
    if opts.mirror:
        chain.append("hflip")
    if opts.speed != 1.0:
        chain.append(f"setpts=PTS/{opts.speed:g}")
    if opts.hook_text:
        chain.extend(_hook_filters(opts, work_dir))
    audio = f"atempo={opts.speed:g}" if opts.speed != 1.0 else None
    return "[0:v]" + ",".join(chain) + "[v]", audio


def render_variant(master: MediaInfo, out: Path, opts: VariantOptions) -> MediaInfo:
    out.parent.mkdir(parents=True, exist_ok=True)
    graph, audio_filter = variant_graph(master, opts, out.parent)
    duration = master.duration / opts.speed
    encode(master, out, graph, duration=duration, audio_filter=audio_filter)
    return probe(out)
