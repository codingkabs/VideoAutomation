"""Burned-in subtitles.

Cues come from a subtitle file you supply (.srt or .vtt) or from automatic
speech-to-text with faster-whisper (optional dependency, runs locally). They
are split into short 2-4 word chunks in the style short-form viewers expect,
styled as ASS, and burned into the video with ffmpeg/libass.

Automatic transcripts are saved next to the renders as ``subtitles.srt``. Edit
that file and post again to fix any word; the edited file is reused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..errors import MediaError
from ..ffmpeg import probe
from ..models import MediaInfo
from .normalize import TARGET_H, TARGET_W, encode

Word = tuple[float, float, str]
Transcriber = Callable[[str, str, str | None], list[Word]]

STYLES = {
    # Big, bold, centred in the lower third: the usual TikTok/Reels look.
    "bold": {"size": 78, "outline": 6, "margin_v": 520, "bold": -1},
    # Smaller captions near the bottom.
    "clean": {"size": 56, "outline": 3, "margin_v": 300, "bold": 0},
}


@dataclass
class Cue:
    start: float
    end: float
    text: str


# ------------------------------------------------------------------ parsing

_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _seconds(stamp: str) -> float:
    m = _TS.search(stamp)
    if not m:
        raise MediaError(f"Bad subtitle timestamp {stamp!r}")
    h, mnt, sec, frac = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(sec) + int(frac.ljust(3, "0")) / 1000


def parse_subtitles(text: str) -> list[Cue]:
    """Parse SRT or WebVTT text."""
    cues: list[Cue] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip())
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        timing = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if timing is None:
            continue
        start_s, end_s = lines[timing].split("-->", 1)
        body = " ".join(re.sub(r"<[^>]+>", "", ln).strip() for ln in lines[timing + 1:])
        if body:
            cues.append(Cue(_seconds(start_s), _seconds(end_s.strip().split(" ")[0]), body))
    if not cues:
        raise MediaError("No subtitle cues found in the file")
    return cues


def load_cues(path: Path) -> list[Cue]:
    if not path.is_file():
        raise MediaError(f"Subtitle file not found: {path}")
    if path.suffix.lower() not in (".srt", ".vtt"):
        raise MediaError("Subtitle files must be .srt or .vtt")
    return parse_subtitles(path.read_text(encoding="utf-8-sig"))


def _stamp_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def write_srt(cues: Iterable[Cue], path: Path) -> Path:
    lines = []
    for i, cue in enumerate(cues, 1):
        lines += [str(i), f"{_stamp_srt(cue.start)} --> {_stamp_srt(cue.end)}", cue.text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------ transcription


def words_to_cues(words: list[Word], max_words: int = 4, max_seconds: float = 2.2) -> list[Cue]:
    """Group word timings into short caption chunks, breaking after punctuation."""
    cues: list[Cue] = []
    chunk: list[Word] = []
    for word in words:
        if not word[2].strip():
            continue
        chunk.append(word)
        text = word[2].strip()
        too_long = chunk[-1][1] - chunk[0][0] >= max_seconds
        if len(chunk) >= max_words or too_long or text[-1:] in ".!?,;:":
            cues.append(Cue(chunk[0][0], chunk[-1][1], " ".join(w[2].strip() for w in chunk)))
            chunk = []
    if chunk:
        cues.append(Cue(chunk[0][0], chunk[-1][1], " ".join(w[2].strip() for w in chunk)))
    return cues


def faster_whisper_transcriber(video_path: str, model_name: str, language: str | None) -> list[Word]:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise MediaError("Automatic subtitles need faster-whisper: pip install 'vauto[subtitles]'") from exc
    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, _info = model.transcribe(video_path, word_timestamps=True, vad_filter=True, language=language)
    words: list[Word] = []
    for segment in segments:
        for w in segment.words or []:
            words.append((float(w.start), float(w.end), w.word))
    return words


def transcribe(video: MediaInfo, model_name: str = "small", language: str | None = None,
               transcriber: Transcriber | None = None) -> list[Cue]:
    words = (transcriber or faster_whisper_transcriber)(video.path, model_name, language)
    if not words:
        raise MediaError("No speech was found for subtitles")
    return words_to_cues(words)


def cues_for(video: MediaInfo, source: str, work: Path, model_name: str, language: str | None,
             transcriber: Transcriber | None = None) -> list[Cue]:
    """``source`` is "auto" (transcribe, cached as subtitles.srt) or a subtitle file path."""
    if source != "auto":
        return load_cues(Path(source))
    cached = work / "subtitles.srt"
    if cached.is_file():
        return load_cues(cached)
    cues = transcribe(video, model_name, language, transcriber)
    write_srt(cues, cached)
    return cues


# ------------------------------------------------------------------ styling


def _stamp_ass(t: float) -> str:
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def cues_to_ass(cues: list[Cue], style: str = "bold", speed: float = 1.0) -> str:
    if style not in STYLES:
        raise MediaError(f"Unknown subtitle style {style!r}; use bold or clean")
    s = STYLES[style]
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {TARGET_W}\nPlayResY: {TARGET_H}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{s['size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        f"{s['bold']},0,0,0,100,100,0,0,1,{s['outline']},0,2,80,80,{s['margin_v']},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events = [
        f"Dialogue: 0,{_stamp_ass(c.start / speed)},{_stamp_ass(c.end / speed)},Default,,0,0,0,,{_ass_text(c.text)}"
        for c in cues
    ]
    return header + "\n".join(events) + "\n"


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def burn_subtitles(src: MediaInfo, out: Path, cues: list[Cue], style: str = "bold",
                   speed: float = 1.0) -> MediaInfo:
    """Render ``cues`` onto ``src``. ``speed`` rescales timings for sped-up variants."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ass = out.with_suffix(".ass")
    ass.write_text(cues_to_ass(cues, style, speed), encoding="utf-8")
    encode(src, out, f"[0:v]ass='{_escape_filter_path(str(ass))}'[v]")
    return probe(out)
