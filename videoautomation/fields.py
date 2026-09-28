"""Every setting vauto reads, grouped for the web settings page."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    help: str = ""
    secret: bool = False
    choices: tuple[str, ...] = ()
    kind: str = "text"  # text | bool | select


ZERNIO_NAMES = (("instagram", "Instagram"), ("facebook", "Facebook"), ("tiktok", "TikTok"),
                ("youtube", "YouTube"), ("snapchat", "Snapchat"), ("threads", "Threads"), ("x", "X"),
                ("linkedin", "LinkedIn"), ("pinterest", "Pinterest"), ("bluesky", "Bluesky"),
                ("telegram", "Telegram"), ("reddit", "Reddit"), ("discord", "Discord"))


def _zernio_accounts() -> tuple[Field, ...]:
    return tuple(Field(f"ZERNIO_ACCOUNT_{key.upper()}", f"{name} account id", "from `vauto accounts`")
                 for key, name in ZERNIO_NAMES)


GROUPS: list[tuple[str, str, tuple[Field, ...]]] = [
    ("general", "General", (
        Field("VAUTO_PLATFORMS", "Default platforms", "comma-separated, e.g. instagram,facebook,tiktok,youtube,snapchat"),
        Field("VAUTO_TIMEZONE", "Time zone", "e.g. Europe/London"),
        Field("VAUTO_BEST_TIMES", "Best posting times", "e.g. mon-fri 07:30,12:30,18:00; sat-sun 10:00"),
        Field("VAUTO_TRIAL_DEFAULT", "Trial Reel on by default", kind="bool"),
        Field("VAUTO_TRIAL_DELAY", "Trial Reel delay (minutes)", "e.g. 60-120"),
        Field("VAUTO_TRIAL_GRADUATION", "Trial Reel graduation", "MANUAL keeps it off followers' feeds",
              choices=("MANUAL", "SS_PERFORMANCE"), kind="select"),
        Field("VAUTO_TRIAL_BACKEND", "Trial Reel route", "auto uses Zernio scheduling when available",
              choices=("auto", "meta", "zernio"), kind="select"),
        Field("VAUTO_TRIAL_REPORT_HOURS", "Trial results check after (hours)", "default 72"),
        Field("VAUTO_STATS_REFRESH_HOURS", "Refresh views and likes every (hours)",
              "default 6; 0 turns it off. Needs `vauto worker` (Docker runs it for you)"),
        Field("VAUTO_DIGEST", "Summary message on Telegram", "weekly (Monday 9am), daily, or off",
              choices=("weekly", "daily", "off"), kind="select"),
        Field("VAUTO_KEEP_FILES_DAYS", "Keep rendered videos for (days)",
              "default 14; older renders, uploads and hand-off files are deleted (thumbnails stay). 0 keeps everything"),
    )),
    ("zernio", "Zernio (TikTok, YouTube, Snapchat and more)", (
        Field("ZERNIO_API_KEY", "API key", "zernio.com > Dashboard > API keys", secret=True),
        Field("ZERNIO_PROFILE_ID", "Profile id", "optional, for best-time analytics"),
        *_zernio_accounts(),
    )),
    ("meta", "Instagram and Facebook (direct)", (
        Field("VAUTO_BACKEND_INSTAGRAM", "Instagram route", choices=("meta", "zernio", "handoff"), kind="select"),
        Field("VAUTO_BACKEND_FACEBOOK", "Facebook route", choices=("meta", "zernio", "handoff"), kind="select"),
        Field("META_GRAPH_HOST", "Graph host", "graph.facebook.com (Facebook Login) or graph.instagram.com",
              choices=("graph.facebook.com", "graph.instagram.com"), kind="select"),
        Field("META_APP_ID", "Meta app id", "only needed for `vauto auth meta` token exchange"),
        Field("META_APP_SECRET", "Meta app secret", secret=True),
        Field("IG_USER_ID", "Instagram user id", "`vauto auth meta` fills this in"),
        Field("IG_ACCESS_TOKEN", "Instagram token", "a Page token never expires", secret=True),
        Field("FB_PAGE_ID", "Facebook Page id"),
        Field("FB_PAGE_ACCESS_TOKEN", "Facebook Page token", secret=True),
    )),
    ("tiktok", "TikTok", (
        Field("VAUTO_BACKEND_TIKTOK", "TikTok route", "tiktok = free direct drafts",
              choices=("zernio", "tiktok", "handoff"), kind="select"),
        Field("TIKTOK_PRIVACY_LEVEL", "Privacy", choices=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
                                                          "FOLLOWER_OF_CREATOR", "SELF_ONLY"), kind="select"),
        Field("TIKTOK_CLIENT_KEY", "Client key", "developers.tiktok.com app, for direct drafts"),
        Field("TIKTOK_CLIENT_SECRET", "Client secret", secret=True),
        Field("TIKTOK_REDIRECT_URI", "Redirect URI", "the https URL registered in your TikTok app"),
    )),
    ("video", "YouTube and Snapchat", (
        Field("YOUTUBE_VISIBILITY", "YouTube visibility", choices=("public", "unlisted", "private"), kind="select"),
        Field("YOUTUBE_ADD_SHORTS_TAG", "Add #Shorts to titles", kind="bool"),
        Field("SNAPCHAT_CONTENT_TYPE", "Snapchat post type", choices=("spotlight", "saved_story", "story"),
              kind="select"),
    )),
    ("telegram", "Telegram (bot, phone hand-off, channel)", (
        Field("TELEGRAM_BOT_TOKEN", "Bot token", "from @BotFather", secret=True),
        Field("TELEGRAM_OWNER_CHAT_ID", "Your chat id", "where hand-offs and notifications go"),
        Field("TELEGRAM_ALLOWED_USER_IDS", "Allowed user ids", "who may use the bot, comma-separated"),
        Field("TELEGRAM_CHANNEL_ID", "Channel to post to", "@channelname or -100… id; add the bot as admin"),
        Field("TELEGRAM_API_BASE", "Bot API server",
              "for phone videos over 20 MB: http://telegram-bot-api:8081 with the docker bigfiles profile"),
        Field("TELEGRAM_API_ID", "Telegram api_id", "my.telegram.org > API development tools; for the big-files server"),
        Field("TELEGRAM_API_HASH", "Telegram api_hash", "for the big-files server", secret=True),
    )),
    ("more", "More platforms", (
        Field("BLUESKY_HANDLE", "Bluesky handle", "e.g. you.bsky.social"),
        Field("BLUESKY_APP_PASSWORD", "Bluesky app password", "Settings > Privacy > App passwords", secret=True),
        Field("THREADS_USER_ID", "Threads user id", "only for the direct Threads route"),
        Field("THREADS_ACCESS_TOKEN", "Threads token", secret=True),
        Field("MASTODON_INSTANCE", "Mastodon instance", "e.g. https://mastodon.social"),
        Field("MASTODON_ACCESS_TOKEN", "Mastodon token", secret=True),
        Field("REDDIT_SUBREDDIT", "Subreddit", "without r/"),
        Field("REDDIT_FLAIR_ID", "Reddit flair id", "if the subreddit requires one"),
        Field("PINTEREST_BOARD_ID", "Pinterest board id", "empty = first board"),
        Field("PINTEREST_LINK", "Pinterest link", "optional destination link on pins"),
    )),
    ("storage", "Public media storage", (
        Field("VAUTO_STORAGE", "Storage", "auto = S3 if set, else Zernio", choices=("auto", "zernio", "s3", "none"),
              kind="select"),
        Field("S3_BUCKET", "Bucket"),
        Field("S3_ENDPOINT_URL", "Endpoint URL", "R2: https://<account>.r2.cloudflarestorage.com"),
        Field("S3_REGION", "Region", "auto for R2"),
        Field("S3_ACCESS_KEY_ID", "Access key id"),
        Field("S3_SECRET_ACCESS_KEY", "Secret access key", secret=True),
        Field("S3_PUBLIC_BASE_URL", "Public base URL", "optional; otherwise links are presigned"),
    )),
    ("extras", "Extras", (
        Field("ANTHROPIC_API_KEY", "Anthropic API key", "for Claude caption rewriting", secret=True),
        Field("VAUTO_CAPTION_MODEL", "Caption model", "default claude-opus-5"),
        Field("VAUTO_SUBTITLE_MODEL", "Subtitle model", "faster-whisper size: tiny, base, small, medium"),
        Field("VAUTO_SUBTITLE_STYLE", "Subtitle style", choices=("bold", "clean"), kind="select"),
        Field("VAUTO_SUBTITLE_LANGUAGE", "Subtitle language", "empty = detect"),
        Field("VAUTO_FONT", "Font for hook text", "path to a .ttf"),
        Field("SAU_COMMAND", "social-auto-upload command", "default sau"),
        Field("SAU_ACCOUNT", "social-auto-upload account", "default default"),
        Field("BILIBILI_TID", "Bilibili category id", "default 160"),
        Field("VAUTO_BROWSER_HEADLESS", "Hide the automation browser", kind="bool"),
        Field("VAUTO_BROWSER_FALLBACK_HANDOFF", "Send to phone if browser automation fails", kind="bool"),
        Field("VAUTO_WEB_PASSWORD", "Web app password", "required when the app is reachable from other devices",
              secret=True),
        Field("VAUTO_PUBLIC_URL", "Phone address", "the https address your phone uses, e.g. from `tailscale serve`"),
    )),
]

ALL_FIELDS: dict[str, Field] = {f.key: f for _, _, fields in GROUPS for f in fields}


def mask(value: str) -> str:
    if not value:
        return ""
    return "•" * 6 + value[-4:] if len(value) > 8 else "•" * 6
