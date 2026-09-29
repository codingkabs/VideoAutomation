"""Plain data types passed between pipeline stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SECONDARY_SURFACES = ("story", "trial_reel", "trial_report")
HDR_TRANSFERS = ("arib-std-b67", "smpte2084")


def label_for(platform: str, surface: str) -> str:
    """Show the surface only when it is not the platform's main post type."""
    return f"{platform} {surface.replace('_', ' ')}" if surface in SECONDARY_SURFACES else platform


@dataclass
class MediaInfo:
    """What ffprobe reports about one file."""

    path: str
    width: int
    height: int
    duration: float
    fps: float
    vcodec: str
    acodec: str | None
    size_bytes: int
    is_image: bool
    color_transfer: str = ""  # e.g. arib-std-b67 (HLG) or smpte2084 (PQ) for HDR phone video

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in HDR_TRANSFERS

    @property
    def has_audio(self) -> bool:
        return self.acodec is not None

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 0.0


@dataclass
class MediaFile:
    """A rendered file ready to publish. ``url`` is set once uploaded to storage."""

    path: str
    kind: str  # "video" or "image"
    url: str | None = None

    @property
    def content_type(self) -> str:
        return "video/mp4" if self.kind == "video" else "image/jpeg"


@dataclass
class PostJob:
    """One post to one platform surface.

    ``surface`` is what gets created: reel, trial_reel, story, carousel, image,
    video, photos, short, spotlight.
    """

    platform: str
    surface: str
    backend: str
    media: list[MediaFile]
    caption: str
    options: dict[str, Any] = field(default_factory=dict)
    post_id: str = ""
    idem_key: str = ""
    run_at: str | None = None  # ISO-8601 UTC; None means now
    depends_on: str | None = None  # idem_key of a job that must publish first

    @property
    def label(self) -> str:
        return label_for(self.platform, self.surface)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PostJob":
        data = dict(data)
        data["media"] = [MediaFile(**m) for m in data.get("media", [])]
        return cls(**data)


@dataclass
class PostResult:
    """Outcome of one job. ``status`` is one of: published, scheduled, queued,
    draft (in the platform's own drafts), held (a draft kept in vauto until you post it),
    failed, skipped, dry_run, duplicate."""

    platform: str
    surface: str
    status: str
    url: str | None = None
    remote_id: str | None = None
    error: str | None = None
    run_at: str | None = None
    notes: list[str] = field(default_factory=list)
    platform_post_id: str | None = None  # the platform's own id (for insights)

    @property
    def ok(self) -> bool:
        return self.status not in ("failed",)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
