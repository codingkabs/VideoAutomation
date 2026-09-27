from videoautomation import captions as cap
from videoautomation.config import platform_spec


def adapt(platform, text, surface="reel", **kw):
    return cap.adapt(platform, text, platform_spec(platform)["caption"], surface, **kw)


def test_instagram_keeps_first_five_hashtags():
    text = "Big news!\n#one #two #three #four #five #six #seven"
    out = adapt("instagram", text)
    assert cap.hashtags(out.text) == ["one", "two", "three", "four", "five"]
    assert "#six" not in out.text and "#seven" not in out.text
    assert out.notes and "#six #seven" in out.notes[0]


def test_hashtag_cap_counts_repeats_once():
    text = "#a #b #A #c #d #e #f"
    out, removed = cap.cap_hashtags(text, 5)
    assert removed == ["f"]
    assert "#A" in out  # a repeat of an allowed tag stays


def test_truncate_cuts_at_word_boundary_with_ellipsis():
    text = "word " * 100
    out = cap.truncate(text.strip(), 50)
    assert len(out) <= 50
    assert out.endswith("…")
    assert not out[:-1].endswith(" ")


def test_truncate_keeps_trailing_hashtag_block():
    body = "This is a long caption sentence. " * 10
    text = body + "\n#keep #these"
    out = cap.truncate_keep_tags(text, 120)
    assert len(out) <= 120
    assert out.endswith("#keep #these")


def test_youtube_title_tags_and_shorts_suffix():
    text = "My best recipe ever #food #cooking\nFull steps below."
    out = adapt("youtube", text, "short")
    assert out.title == "My best recipe ever #Shorts"
    assert out.tags == ["food", "cooking"]
    assert "Full steps below." in out.text


def test_youtube_title_respects_100_chars():
    out = adapt("youtube", "x" * 300, "short")
    assert len(out.title) <= 100


def test_youtube_no_duplicate_shorts_tag():
    out = adapt("youtube", "Title here #shorts", "short")
    assert out.title == "Title here"


def test_tiktok_photo_title_and_description():
    text = "A" * 120 + "\nmore detail #tag"
    out = adapt("tiktok", text, "photos")
    assert len(out.title) <= 90
    assert "more detail" in out.text


def test_tiktok_counts_utf16_units():
    assert cap.text_length("😀", "utf16") == 2
    out = cap.truncate("😀" * 2000, 2200, "utf16")
    assert cap.text_length(out, "utf16") <= 2200


def test_snapchat_spotlight_160_and_story_empty():
    long = "Spotlight caption " * 20
    assert len(adapt("snapchat", long, "spotlight").text) <= 160
    story = adapt("snapchat", long, "story", snapchat_content_type="story")
    assert story.text == ""


def test_short_caption_unchanged():
    out = adapt("facebook", "Hello #world")
    assert out.text == "Hello #world"
    assert out.notes == []
