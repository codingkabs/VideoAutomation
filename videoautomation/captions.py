"""Per-platform caption adaptation.

Rule-based by default: hashtag caps, length limits, and title extraction.
Optionally Claude rewrites the caption per platform first (``--rewrite-captions``);
the rules still run afterwards so limits are always enforced.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import VautoError

HASHTAG_RE = re.compile(r"(?<![\w#])#(\w+)", re.UNICODE)
ELLIPSIS = "…"


@dataclass
class PlatformCaption:
    text: str
    title: str | None = None
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------ helpers


def text_length(text: str, unit: str = "chars") -> int:
    """TikTok counts UTF-16 code units; other platforms count characters."""
    if unit == "utf16":
        return len(text.encode("utf-16-le")) // 2
    return len(text)


def hashtags(text: str) -> list[str]:
    """Unique hashtags in order of first appearance, without the '#'."""
    seen: dict[str, str] = {}
    for tag in HASHTAG_RE.findall(text):
        seen.setdefault(tag.lower(), tag)
    return list(seen.values())


def _tidy(text: str) -> str:
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def cap_hashtags(text: str, max_tags: int | None) -> tuple[str, list[str]]:
    """Keep the first ``max_tags`` distinct hashtags and delete the rest."""
    if max_tags is None:
        return text, []
    keep = {t.lower() for t in hashtags(text)[:max_tags]}
    removed: list[str] = []

    def repl(match: re.Match[str]) -> str:
        tag = match.group(1)
        if tag.lower() in keep:
            return match.group(0)
        removed.append(tag)
        return ""

    return _tidy(HASHTAG_RE.sub(repl, text)), removed


def truncate(text: str, max_len: int, unit: str = "chars") -> str:
    """Cut at a word boundary and add an ellipsis. Returns ``text`` if it fits."""
    if text_length(text, unit) <= max_len:
        return text
    budget = max_len - 1  # room for the ellipsis
    cut = text[:budget]
    while text_length(cut, unit) > budget:
        cut = cut[:-1]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space >= int(len(cut) * 0.6):
        cut = cut[:space]
    return cut.rstrip(" \n,;:-") + ELLIPSIS


def truncate_keep_tags(text: str, max_len: int, unit: str = "chars") -> str:
    """Truncate the body but keep a trailing block of hashtags intact when possible."""
    if text_length(text, unit) <= max_len:
        return text
    lines = text.rstrip().split("\n")
    tail: list[str] = []
    while lines and lines[-1].strip() and all(w.startswith("#") for w in lines[-1].split()):
        tail.insert(0, lines.pop())
    tag_block = "\n".join(tail)
    body = "\n".join(lines).rstrip()
    room = max_len - (text_length(tag_block, unit) + 2 if tag_block else 0)
    if not tag_block or room < max_len * 0.4:
        return truncate(text, max_len, unit)
    return truncate(body, room, unit) + "\n\n" + tag_block


def title_from(text: str, max_len: int) -> str:
    """First non-empty line with hashtags removed, cut to ``max_len``."""
    for line in text.splitlines():
        cleaned = _tidy(HASHTAG_RE.sub("", line))
        if cleaned:
            return truncate(cleaned, max_len)
    return ""


def tags_within(tags: list[str], max_chars: int) -> list[str]:
    """YouTube counts tags plus separators against a 500-character budget."""
    out: list[str] = []
    used = 0
    for tag in tags:
        cost = len(tag) + (1 if out else 0)
        if used + cost > max_chars:
            break
        out.append(tag)
        used += cost
    return out


# ------------------------------------------------------------------------ adapter


def adapt(
    platform: str,
    caption: str,
    spec: dict[str, Any],
    surface: str,
    *,
    youtube_shorts_tag: bool = True,
    snapchat_content_type: str = "spotlight",
) -> PlatformCaption:
    """Fit ``caption`` to one platform's rules. ``spec`` is the platform's ``caption`` block."""
    caption = _tidy(caption)
    max_chars = spec.get("max_chars")
    notes: list[str] = []

    text, removed = cap_hashtags(caption, spec.get("max_hashtags"))
    if removed:
        notes.append(f"removed {len(removed)} hashtag(s) over the limit of {spec['max_hashtags']}: "
                     + " ".join("#" + t for t in removed))

    if platform == "youtube":
        suffix = " #Shorts" if youtube_shorts_tag and "#shorts" not in caption.lower() else ""
        title_max = spec.get("title_max", 100)
        title = title_from(text, title_max - len(suffix)) or "New Short"
        tags = tags_within(hashtags(caption), spec.get("tags_max_chars", 500))
        description = truncate_keep_tags(text, max_chars) if max_chars else text
        return PlatformCaption(text=description, title=title + suffix, tags=tags, notes=notes)

    if platform == "tiktok" and surface == "photos":
        title = title_from(text, spec.get("photo_title_max", 90))
        description = truncate_keep_tags(text, spec.get("photo_description_max", 4000), "utf16")
        return PlatformCaption(text=description, title=title, notes=notes)

    if platform == "snapchat":
        if snapchat_content_type == "story":
            return PlatformCaption(text="", notes=notes + ["Snapchat Stories take no caption"])
        if snapchat_content_type == "saved_story":
            title = title_from(text, spec.get("saved_story_title_max", 45))
            return PlatformCaption(text=title, title=title, notes=notes)

    unit = spec.get("unit", "chars")
    title = title_from(text, spec["title_max"]) if spec.get("title_max") else None
    tags = hashtags(text) if spec.get("tags") else []  # after the cap, so tags respect it
    if tags:
        # Platforms with a separate tag field get the body without hashtags.
        text = _tidy(HASHTAG_RE.sub("", text))
    if max_chars and text_length(text, unit) > max_chars:
        text = truncate_keep_tags(text, max_chars, unit)
        notes.append(f"shortened to {max_chars} characters")
    return PlatformCaption(text=text, title=title, tags=tags, notes=notes)


# ------------------------------------------------------------- Claude rewriting

COMMENT_SYSTEM = """You write the first comment a creator posts under their own short-form video.

Look at the frames from the video and read the caption. Write one short, natural comment in the creator's voice that fits this video: a question that invites replies, a fun detail, or a nudge to watch again. Keep it under 150 characters, at most one emoji, no hashtags, no links, and do not repeat the caption. Keep the language of the caption.

Return only the JSON object."""


def first_comment(caption: str, frames: list[Path], model: str, api_key: str | None = None) -> str:
    """Ask Claude for a short first comment that fits the video. Raises VautoError on any failure."""
    import base64

    try:
        import anthropic
    except ImportError as exc:
        raise VautoError("Claude comments need the anthropic package: pip install 'vauto[claude]'") from exc

    content: list[dict[str, Any]] = []
    for frame in frames[:4]:
        data = base64.standard_b64encode(frame.read_bytes()).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    content.append({"type": "text", "text": f"Caption:\n<caption>\n{caption or '(no caption)'}\n</caption>"})
    schema = {"type": "object", "properties": {"comment": {"type": "string"}},
              "required": ["comment"], "additionalProperties": False}

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=4000,
            system=COMMENT_SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        raise VautoError(f"Could not reach the Claude API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise VautoError(f"Claude API error {exc.status_code}: {exc.message}") from exc
    if response.stop_reason == "refusal":
        raise VautoError("Claude declined to write a comment for this video")
    if response.stop_reason == "max_tokens":
        raise VautoError("Claude's comment was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        comment = str(json.loads(text).get("comment") or "").strip()
    except (json.JSONDecodeError, AttributeError) as exc:
        raise VautoError("Claude returned a comment that was not valid JSON") from exc
    if not comment:
        raise VautoError("Claude returned an empty comment")
    return truncate(comment, 300)


REWRITE_SYSTEM = """You adapt one short-form video caption for several social platforms.

Keep the creator's meaning, voice, facts, @mentions and links. Do not invent claims, offers, links or hashtags that change the meaning. Keep the language of the original caption.

For each platform, write the caption that platform's audience expects, within its limits:
- instagram: hook in the first line, at most 5 hashtags.
- facebook: conversational, few or no hashtags.
- tiktok: punchy, a few relevant hashtags.
- youtube: first line is the video title (under 90 characters, no hashtags); the rest is the description.
- snapchat: one short line, at most 160 characters in total.
- x, bluesky, threads, mastodon: short and conversational; threads allows one hashtag.
- linkedin: professional tone, a clear takeaway, few hashtags.
- pinterest: first line is the pin title; describe what the viewer gets.
- reddit: first line is the post title; no hashtags.
- any other platform: follow its usual style and stay within its limit.

Return only the JSON object."""


def rewrite_captions(caption: str, platforms: list[str], limits: dict[str, int], model: str,
                     api_key: str | None = None) -> dict[str, str]:
    """Ask Claude for one caption per platform. Raises VautoError on any failure,
    so callers can fall back to the original caption."""
    try:
        import anthropic
    except ImportError as exc:
        raise VautoError("Caption rewriting needs the anthropic package: pip install 'vauto[claude]'") from exc

    schema = {
        "type": "object",
        "properties": {p: {"type": "string"} for p in platforms},
        "required": list(platforms),
        "additionalProperties": False,
    }
    limit_lines = "\n".join(f"- {p}: at most {limits[p]} characters" for p in platforms if p in limits)
    user = f"Platforms and limits:\n{limit_lines}\n\nOriginal caption:\n<caption>\n{caption}\n</caption>"

    # A key from .env is passed explicitly; otherwise the SDK finds credentials itself.
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=REWRITE_SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as exc:
        raise VautoError(f"Could not reach the Claude API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise VautoError(f"Claude API error {exc.status_code}: {exc.message}") from exc

    if response.stop_reason == "refusal":
        raise VautoError("Claude declined to rewrite this caption")
    if response.stop_reason == "max_tokens":
        raise VautoError("Claude's caption rewrite was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise VautoError("Claude returned captions that were not valid JSON") from exc
    return {p: str(data.get(p) or caption) for p in platforms}
