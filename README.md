# vauto: post once, publish everywhere

Give `vauto` one video (or a set of photos) and a caption. It formats the media for each platform, adapts the caption to each platform's rules, and publishes to Instagram, Facebook, TikTok, YouTube Shorts and Snapchat in one go.

On Instagram it can also post a **Trial Reel** 1–2 hours later. That is a zoomed-in version of the same video, shown to non-followers first.

The platform research behind this tool, covering about 50 platforms in the UK and worldwide, is in [docs/platform-research.md](docs/platform-research.md).

## What it does

| Platform | Video | Photos | Route |
|---|---|---|---|
| Instagram | Reel, optional Story, optional Trial Reel | Carousel (up to 10) or single image | Meta Graph API (direct), or Zernio |
| Facebook Page | Reel | Single or multi-photo post | Meta Graph API (direct), or Zernio |
| TikTok | Video, or send to drafts | Photo post with auto trending music | Zernio |
| YouTube Shorts | Short with title and tags | Slideshow video | Zernio |
| Snapchat | Spotlight, Saved Story or Story | Slideshow video | Zernio |

For every post it will:

- Convert the video to a vertical 1080×1920 H.264/AAC master. Landscape footage goes over a blurred fill, and near-vertical footage is cropped.
- Check each platform's length and size limits. It trims (`--trim`) or reports the problem, so one platform's limit never blocks the rest.
- Fit the caption. Instagram keeps 5 hashtags, YouTube gets a title plus `#Shorts` and tags, Snapchat gets 160 characters, and TikTok photo posts get a 90-character title.
- Publish in parallel and print a link or a clear error for every platform.
- Remember what it posted, so running the same command twice does not double-post.

## Setup

### 1. Install

Python 3.10+ and ffmpeg are required.

```bash
# macOS: brew install ffmpeg      Ubuntu: sudo apt install ffmpeg
pip install -e ".[all]"
cp .env.example .env
```

### 2. Connect TikTok, YouTube and Snapchat through Zernio

These three platforms keep API posts private until you pass their audits (Snapchat is invite-only). Zernio is already approved.

1. Create an account at zernio.com. The first 2 accounts are free, then about $6 per account per month.
2. Connect TikTok, YouTube and your Snapchat Public Profile.
3. Create an API key and put it in `.env` as `ZERNIO_API_KEY`.
4. Run `vauto accounts` and copy the printed `ZERNIO_ACCOUNT_...` lines into `.env`.

Zernio also hosts uploaded media, so no other storage is needed for these platforms.

### 3. Connect Instagram and Facebook

**Option A, simplest:** connect Instagram and Facebook in Zernio too. Set these in `.env`:

```
VAUTO_BACKEND_INSTAGRAM=zernio
VAUTO_BACKEND_FACEBOOK=zernio
ZERNIO_ACCOUNT_INSTAGRAM=...
ZERNIO_ACCOUNT_FACEBOOK=...
```

**Option B, free, direct Meta API** (the default):

1. Your Instagram account must be a Business or Creator account linked to a Facebook Page.
2. Create an app at developers.facebook.com (type: Business). Add Instagram and Facebook Login for Business.
3. In the Graph API Explorer, generate a user token with `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` and `pages_manage_posts`. Exchange it for a long-lived token.
4. `GET /me/accounts` gives your Page id and a Page access token. Put them in `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN`.
5. `GET /{page-id}?fields=instagram_business_account` gives `IG_USER_ID`. Put the long-lived user token in `IG_ACCESS_TOKEN`.

Long-lived Instagram user tokens last 60 days, so refresh them before they expire. Your own accounts work while the app is in development mode, with no App Review.

Reels and Trial Reels upload straight from disk. **Instagram photo posts need a public URL**, which comes from Zernio's media hosting (when `ZERNIO_API_KEY` is set) or from an R2/S3 bucket (`S3_*` settings).

### 4. Check everything without posting

```bash
vauto post my-video.mp4 -c "My caption #uk" --trial --dry-run
```

This renders all the media and prints what each platform would receive.

## Posting

```bash
# One video everywhere
vauto post clip.mp4 -c "Morning routine that changed my life ☀️ #morning #routine"

# Plus a Trial Reel 60-120 minutes later, zoomed 15%, with a hook caption
vauto post clip.mp4 -c "..." --trial --trial-hook "Wait for the end"

# Send TikTok to drafts so you can add a trending sound in the app
vauto post clip.mp4 -c "..." --tiktok-draft

# Photos: carousel on Instagram, photo post on TikTok, slideshow on YouTube/Snapchat
vauto post 1.jpg 2.jpg 3.jpg -c "Which outfit? #autumn" --audio track.mp3

# Only some platforms, with a different TikTok caption
vauto post clip.mp4 -c "..." -p instagram,tiktok --caption-for tiktok="shorter tiktok caption #fyp"

# Let Claude tailor the caption for each platform (needs ANTHROPIC_API_KEY)
vauto post clip.mp4 -c "..." --rewrite-captions
```

Other commands:

| Command | What it does |
|---|---|
| `vauto jobs` | Recent posts with links, errors and scheduled times |
| `vauto worker` | Posts queued jobs when due (`--once` for cron) |
| `vauto variant clip.mp4 -o preview.mp4 --mode push --hook "Part 2"` | Preview a Trial Reel variant locally |
| `vauto platforms` | The full researched platform list and what is implemented |
| `vauto accounts` | Accounts connected in Zernio |
| `vauto probe file` | Media details |

### Using it through Claude Code

Drop the video into a Claude Code session in this repository and say something like *"post this everywhere with the caption '…', and do a trial reel"*. Claude runs `vauto post` and reports the links.

## Trial Reels

A Trial Reel is shown to non-followers first. It needs a public professional account with 1,000+ followers.

- The variant is visibly different from the main Reel. `--trial-mode static` zooms in (default 1.15×), and `--trial-mode push` slowly zooms in over the clip. Optional extras are `--trial-hook` text, `--trial-mirror` and `--trial-speed 1.03`. It also gets a different cover frame. Instagram demotes near-duplicate content, so the difference matters.
- `--trial-graduation MANUAL` (default) keeps it away from followers until you share it in the app. `SS_PERFORMANCE` lets Instagram share it automatically if it performs well.
- The delay is random within `--trial-delay 60-120` minutes.
- It is only posted if the main Reel published successfully.

**Where the delayed post waits:**

- With an Instagram account connected in Zernio (`ZERNIO_ACCOUNT_INSTAGRAM`), the Trial Reel is scheduled on Zernio's servers. Nothing on your machine needs to keep running. This is the default whenever that account is set.
- Otherwise it waits in the local queue in `~/.vauto/jobs.db`. Then `vauto worker` must be running on the same machine at the scheduled time, or a cron entry must call it:

  ```
  */5 * * * * cd /path/to/VideoAutomation && vauto worker --once
  ```

  Cloud Claude Code sessions are temporary, so use the Zernio route there.

## Music

No platform lets an API add songs from its music library. The audio in your file is what gets posted.

- Bake music into the video before posting, using original or royalty-free audio.
- Use `--tiktok-draft` to add a trending TikTok sound in the app.
- TikTok photo posts get a trending sound automatically.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests render real media with ffmpeg and use fake HTTP sessions, so no platform is contacted.

## Roadmap

- **Phase 2:** Threads, X, LinkedIn, Pinterest, Bluesky, Telegram, Reddit, Tumblr, Mastodon, VK, Dailymotion, Vimeo, Rumble. Most are already reachable through Zernio or simple official APIs.
- **Phase 3:** browser automation for platforms without APIs (Lemon8, Likee, Kwai, Moj, Josh, Naver Clip and others), for the accounts you actually have.
- **Phase 4:** mainland China platforms (Douyin, Kuaishou, Xiaohongshu, WeChat Channels, Bilibili) through `social-auto-upload`, once local accounts exist.
