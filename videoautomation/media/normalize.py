"""Turn any input video into a vertical 1080x1920 H.264/AAC master."""

from __future__ import annotations

from pathlib import Path

from ..errors import MediaError
from ..ffmpeg import probe, run_ffmpeg
from ..models import MediaInfo

TARGET_W, TARGET_H = 1080, 1920
FIT_MODES = ("auto", "crop", "pad", "blur")


def resolve_fit(mode: str, src_aspect: float, target_aspect: float) -> str:
    """``auto`` crops when the source is already close to the target shape,
    otherwise it places the video over a blurred copy of itself."""
    if mode not in FIT_MODES:
        raise MediaError(f"Unknown fit mode {mode!r}; use one of {', '.join(FIT_MODES)}")
    if mode != "auto":
        return mode
    if target_aspect and abs(src_aspect - target_aspect) / target_aspect <= 0.03:
        return "crop"
    return "blur"


def fit_graph(in_label: str, out_label: str, mode: str, w: int, h: int, prefix: str = "f") -> str:
    """Filter graph that fits ``[in_label]`` into a w x h frame as ``[out_label]``."""
    if mode == "crop":
        return (
            f"[{in_label}]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1[{out_label}]"
        )
    if mode == "pad":
        return (
            f"[{in_label}]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[{out_label}]"
        )
    if mode == "blur":
        return (
            f"[{in_label}]split=2[{prefix}bg0][{prefix}fg0];"
            f"[{prefix}bg0]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=luma_radius=40:luma_power=2[{prefix}bg];"
            f"[{prefix}fg0]scale={w}:{h}:force_original_aspect_ratio=decrease[{prefix}fg];"
            f"[{prefix}bg][{prefix}fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out_label}]"
        )
    raise MediaError(f"Unknown fit mode {mode!r}")


def output_fps(src_fps: float) -> float | None:
    """Keep the source rate when platforms accept it (24-60), else force 30."""
    if 23.0 <= src_fps <= 60.0:
        return None
    return 30.0


def video_codec_args(video_bitrate_k: int | None = None) -> list[str]:
    args = [
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-profile:v", "high",
    ]
    if video_bitrate_k:
        args += ["-b:v", f"{video_bitrate_k}k", "-maxrate", f"{int(video_bitrate_k * 1.2)}k",
                 "-bufsize", f"{video_bitrate_k * 2}k"]
    else:
        args += ["-crf", "20", "-maxrate", "12M", "-bufsize", "24M"]
    return args


AUDIO_ARGS = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]
CONTAINER_ARGS = ["-movflags", "+faststart"]


def encode(
    src: MediaInfo,
    out: Path,
    video_graph: str,
    *,
    duration: float | None = None,
    video_bitrate_k: int | None = None,
    audio_filter: str | None = None,
) -> Path:
    """Run one encode. ``video_graph`` reads ``[0:v]`` and writes ``[v]``.

    Inputs without audio get a silent stereo track, because some platforms
    reject video files with no audio stream.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    total = duration if duration is not None else src.duration
    args: list[str] = ["-i", src.path]
    if src.has_audio:
        graph = video_graph
        if audio_filter:
            graph += f";[0:a]{audio_filter}[a]"
            audio_map = "[a]"
        else:
            audio_map = "0:a:0"
    else:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        graph = video_graph
        audio_map = "1:a:0"
    args += ["-filter_complex", graph, "-map", "[v]", "-map", audio_map]
    args += video_codec_args(video_bitrate_k) + AUDIO_ARGS + CONTAINER_ARGS
    if total:
        args += ["-t", f"{total:.3f}"]
    args.append(str(out))
    run_ffmpeg(args)
    return out


def normalize_video(src: MediaInfo, out: Path, fit: str = "auto") -> tuple[Path, MediaInfo]:
    """Render the vertical master every other rendition is derived from."""
    if src.is_image:
        raise MediaError(f"{Path(src.path).name} is an image, not a video")
    if src.duration <= 0:
        raise MediaError(f"{Path(src.path).name} has no measurable duration")
    mode = resolve_fit(fit, src.aspect, TARGET_W / TARGET_H)
    graph = fit_graph("0:v", "vfit", mode, TARGET_W, TARGET_H)
    fps = output_fps(src.fps)
    graph += f";[vfit]{'fps=' + str(fps) if fps else 'null'}[v]"
    encode(src, out, graph)
    return out, probe(out)
