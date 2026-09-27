# vauto: post once, publish everywhere

Give `vauto` one video (or a set of photos) and a caption. It formats the media for each platform, fits the caption to each platform's rules, and publishes to every platform you pick: Instagram, Facebook, TikTok, YouTube Shorts, Snapchat and about 40 more.

- **Instagram Trial Reels.** A zoomed-in version of your video goes out 1–2 hours later to non-followers, and vauto tells you 72 hours later which version won.
- **Made for phone editors:** finish your edit in CapCut or your gallery, tap **Share → vauto**, write the caption, post. It also works as a web app on your computer, a Telegram bot, or the command line (which Claude Code can drive for you).
- **Subtitles, scheduling, best posting times, TikTok drafts for trending sounds, and per-platform captions** are built in.

The research behind it, covering about 50 platforms, is in [docs/platform-research.md](docs/platform-research.md).

---

## Contents

1. [What it posts where](#what-it-posts-where)
2. [Use it from your phone](#use-it-from-your-phone)
3. [Install](#install)
4. [Run it 24/7 with Docker](#run-it-247-with-docker)
5. [Setup, step by step](#setup-step-by-step)
6. [Using it](#using-it)
7. [Trial Reels](#trial-reels)
8. [Scheduling and best times](#scheduling-and-best-times)
9. [Subtitles and music](#subtitles-and-music)
10. [Phone-only apps, web uploaders and China](#phone-only-apps-web-uploaders-and-china)
11. [Keeping it running](#keeping-it-running)
12. [Troubleshooting](#troubleshooting)

---

## What it posts where

| Group | Platforms | How |
|---|---|---|
| UK core | Instagram (Reels, carousels, Stories, Trial Reels), Facebook Page, TikTok, YouTube Shorts, Snapchat | Official APIs: Meta directly, the rest through Zernio |
| Global | Threads, X, LinkedIn, Pinterest, Bluesky, Telegram channel, Reddit, Mastodon, Discord | Zernio, or free direct APIs (Bluesky, Telegram, Mastodon, Threads) |
| Web uploaders (beta) | Rutube, Likee, Dzen, Naver Clip | Browser automation with your saved login; falls back to your phone if a site changes |
| Phone-only apps | Lemon8, Kwai, Triller, Moj, Josh, Chingari, ShareChat, Roposo, Snack Video, LINE VOOM, Yappy, Zalo, Clapper, Fanbase, WhatsApp Channels, Facebook Groups | Hand-off: the ready file and caption arrive on your phone, you tap post |
| China (beta) | Douyin, Kuaishou, Xiaohongshu, WeChat Channels, Bilibili, Weibo, Baijiahao | social-auto-upload (needs Chinese accounts) |

For every post vauto:

- converts video to vertical 1080×1920 H.264/AAC, over a blurred fill if it was landscape;
- checks each platform's length and size limits, trimming (`--trim`), shrinking the file, or skipping just that platform with a clear reason;
- fits the caption: Instagram keeps 5 hashtags, YouTube gets a title plus `#Shorts` and tags, X gets 280 characters, Snapchat 160, Reddit a title with no hashtags;
- turns photo sets into carousels where supported and slideshows where not;
- publishes in parallel, and reports a link or a plain-English error for each platform;
- never double-posts: the same media and caption is recognised on a re-run.

Run `vauto platforms` to see every platform and whether it is set up.

---

## Use it from your phone

If you edit on your phone, this is the everyday flow:

1. Export your edit from CapCut, InShot or your gallery.
2. Tap **Share → vauto** (or share it to your vauto Telegram bot).
3. The vauto app opens with the video loaded. Write the caption, tick the platforms and options, tap **Preview**, then **Post**.

Your phone only sends the video; the work happens on a computer (or small server) that stays on. One-time setup:

**1. Keep vauto running somewhere.** A home computer that stays on, a Mac mini, or a small cloud server (about £4 a month) all work. The easiest way is Docker: see [Run it 24/7 with Docker](#run-it-247-with-docker).

**2. Set a password.** Set `VAUTO_WEB_PASSWORD` in `.env` (or in the Setup tab). vauto refuses to open to other devices without one.

**3. Give it a secure address your phone can reach.** Phones only install web apps and accept shares over https. The simplest free option is [Tailscale](https://tailscale.com):

- Install Tailscale on the computer and on your phone, and sign in to both with the same account.
- On the computer, run `tailscale serve --bg 8765`. It prints an address like `https://your-pc.tail1234.ts.net`.
- Set `VAUTO_PUBLIC_URL` to that address.

This works from anywhere (home, mobile data, abroad) and nobody else can reach it. `vauto phone` prints all your exact values, including the ones for the iPhone Shortcut below.

**4. Add vauto to your phone.**

| Phone | How | Sharing into vauto |
|---|---|---|
| **Android** | Open your address in Chrome, menu → **Install app** | Built in: **Share → vauto** from Gallery, CapCut, Files and more |
| **iPhone** | Open your address in Safari, **Share → Add to Home Screen** | iPhones don't let web apps appear in the share sheet, so add a Shortcut (below), or pick the video from Photos inside the app |
| **Either** | Telegram: share the video to your vauto bot | See [Telegram bot](#telegram-bot); videos over 20 MB need the big-files server |

**iPhone Shortcut for the share sheet** (about 2 minutes, once):

1. Open **Shortcuts** → **+** → name it *Post with vauto*.
2. Tap the **(i)**, turn on **Show in Share Sheet**, and set it to accept **Media**.
3. Add **Get Contents of URL**:
   - URL: `https://your-address/share?format=json`
   - Method: **POST**, Request Body: **Form**
   - Add a **File** field named `files` set to **Shortcut Input**
   - Optional: add a **Text** field named `text` set to **Ask for Input**, to type the caption straight away
   - Headers: `Authorization` = the `Basic …` value printed by `vauto phone`
4. Add **Get Dictionary Value**, key `open_url`, from *Contents of URL*.
5. Add **Open URLs**.

Now **Share → Post with vauto** uploads the video and opens vauto with it loaded. Nothing is posted until you tap Post.

**Big videos through Telegram.** Standard Telegram bots can only download 20 MB, which many phone edits exceed. The Docker setup includes Telegram's own server in "big files" mode (up to 2 GB):

1. Get an `api_id` and `api_hash` at [my.telegram.org](https://my.telegram.org) → API development tools, and set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and `TELEGRAM_API_BASE=http://telegram-bot-api:8081`.
2. Move your bot off Telegram's cloud server once: open `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/logOut` in a browser.
3. Start it: `docker compose --profile bot --profile bigfiles up -d`.

---

## Install

You need Python 3.10+ and ffmpeg.

```bash
# macOS: brew install ffmpeg      Ubuntu/Debian: sudo apt install ffmpeg      Windows: winget install ffmpeg
git clone <this repo> && cd VideoAutomation
pip install -e ".[all]"
playwright install chromium        # only for Rutube/Likee/Dzen/Naver Clip
cp .env.example .env
vauto doctor                       # shows what is ready and what is missing
```

`.[all]` adds the web app, Claude caption rewriting, automatic subtitles, browser automation and S3/R2 storage. Smaller installs: `.[web]`, `.[claude]`, `.[subtitles]`, `.[browser]`, `.[s3]`.

---

## Run it 24/7 with Docker

This runs the web app, the worker (scheduled posts, Trial Reels, results checks) and optionally the Telegram bot, restarting them automatically.

```bash
git clone <this repo> && cd VideoAutomation
cp .env.example .env          # set VAUTO_WEB_PASSWORD at least; everything else can be done in the app
mkdir -p data
docker compose up -d          # web app on port 8765 + worker
docker compose --profile bot up -d                        # + Telegram bot
docker compose --profile bot --profile bigfiles up -d     # + phone videos over 20 MB via Telegram
```

- Open `http://localhost:8765` on the computer, or your Tailscale address on your phone.
- Settings live in `.env`; renders, the queue and saved tokens live in `./data`.
- After changing settings in the web app, run `docker compose restart` so the worker and bot pick them up.
- Add `--build-arg EXTRAS=all` to `docker compose build` for automatic subtitles in the image (larger).
- Browser-automation platforms (Rutube, Likee, Dzen, Naver Clip) need a visible browser for the one-time login, so run those from a normal install on a desktop.

---

## Setup, step by step

Every setting can be filled in from the web app (`vauto web`, then **Setup**), or by editing `.env`. `vauto doctor` (or the Setup tab) shows a tick or a fix for each item. Set up only the platforms you use.

### 1. Zernio: TikTok, YouTube, Snapchat (and optionally more)

TikTok and YouTube keep API uploads private until an app passes their audit, and Snapchat is invite-only. Zernio is already approved.

1. Create an account at zernio.com (2 accounts free, then about $6 per account per month).
2. Connect TikTok, YouTube and your Snapchat Public Profile. Connect X, LinkedIn, Pinterest, Reddit, Threads and Discord too if you want them.
3. Create an API key and set `ZERNIO_API_KEY`.
4. Run `vauto accounts` and copy the printed `ZERNIO_ACCOUNT_...` lines into `.env`.

Zernio also hosts uploaded media, so no other storage is needed.

### 2. Instagram and Facebook

**Easiest:** connect them in Zernio too, and set `VAUTO_BACKEND_INSTAGRAM=zernio`, `VAUTO_BACKEND_FACEBOOK=zernio` and their `ZERNIO_ACCOUNT_...` ids.

**Free, direct Meta API (default):**

1. Make your Instagram a Business or Creator account and link it to your Facebook Page.
2. Create an app at developers.facebook.com (type Business). Set `META_APP_ID` and `META_APP_SECRET`.
3. In the Graph API Explorer, create a user token with `instagram_basic`, `instagram_content_publish`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement` and `pages_manage_posts`.
4. Run:
   ```bash
   vauto auth meta --user-token "<that token>" --write
   ```
   This finds your Page and its linked Instagram, and saves Page tokens that **never expire** into `.env`.

Tokens from Instagram Login (`META_GRAPH_HOST=graph.instagram.com`) last 60 days; vauto refreshes them automatically.

> Tip: even with the Meta route, connecting Instagram in Zernio (`ZERNIO_ACCOUNT_INSTAGRAM`) makes Trial Reels schedule on Zernio's servers, so nothing has to keep running on your computer.

### 3. TikTok drafts for free (optional)

To send videos to your TikTok inbox without Zernio (so you can add a trending sound):

1. Create an app at developers.tiktok.com with Login Kit and the Content Posting API, scope `video.upload`. No audit is needed for drafts.
2. Set `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI` and `VAUTO_BACKEND_TIKTOK=tiktok`.
3. Run `vauto auth tiktok`, open the link, approve, then `vauto auth tiktok --code "<the URL you were sent to>"`.

### 4. Telegram: phone bot, hand-offs and notifications (recommended)

1. Message @BotFather, create a bot, set `TELEGRAM_BOT_TOKEN`.
2. Start `vauto bot`, message your bot once; it replies with your user id. Set `TELEGRAM_ALLOWED_USER_IDS` and `TELEGRAM_OWNER_CHAT_ID` to it.
3. To post to a Telegram channel too, add the bot as a channel admin and set `TELEGRAM_CHANNEL_ID`.

### 5. Other free platforms

| Platform | Settings |
|---|---|
| Bluesky | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` (Settings > Privacy > App passwords) |
| Mastodon | `MASTODON_INSTANCE`, `MASTODON_ACCESS_TOKEN` (Preferences > Development) |
| Threads direct | `VAUTO_BACKEND_THREADS=threads`, `THREADS_USER_ID`, `THREADS_ACCESS_TOKEN` |
| Reddit | `REDDIT_SUBREDDIT` (and `REDDIT_FLAIR_ID` if required) |
| Pinterest | `PINTEREST_BOARD_ID` (optional) |

### 6. Storage for public links (only if needed)

Instagram photo posts on the direct Meta route, and Threads direct, need media at a public URL. With `ZERNIO_API_KEY` set, Zernio hosts it. Otherwise set an S3 or Cloudflare R2 bucket (`S3_*` settings).

---

## Using it

### Web app

```bash
vauto web          # opens on http://127.0.0.1:8765
```

- **Post:** drop a video or photos, write the caption (live length and hashtag checks per platform), pick platforms, tick options, **Preview** to see every version with its caption and media, then **Post**.
- **Queue:** everything posted, scheduled or waiting; cancel or retry.
- **Trial results:** main Reel vs trial, side by side.
- **Setup:** what's ready, and every setting.
- **Platforms:** all 50, their route and limits.

To use it from your phone, see [Use it from your phone](#use-it-from-your-phone). Quick version on the same Wi-Fi: set `VAUTO_WEB_PASSWORD`, then `vauto web --host 0.0.0.0`.

### Telegram bot

```bash
vauto bot
```

Send your bot a video (as a **file**, so Telegram doesn't compress it) or photos, with the caption. Tap the buttons to turn on the Trial Reel, TikTok drafts, subtitles or best-time scheduling, **Preview**, then **Post**. Links come back in the chat. Standard Telegram bots can only download files up to 20 MB; for bigger videos use the vauto app or the big-files server described in [Use it from your phone](#use-it-from-your-phone).

### Command line

```bash
vauto post clip.mp4 -c "Morning routine that changed my life ☀️ #morning"      # everywhere
vauto post clip.mp4 -c "..." --dry-run                                           # preview only
vauto post clip.mp4 -c "..." --trial --trial-hook "Wait for the end"             # + Trial Reel
vauto post clip.mp4 -c "..." --at best                                           # next best time
vauto post clip.mp4 -c "..." --subtitles auto                                    # burned-in captions
vauto post clip.mp4 -c "..." -p instagram,tiktok,x --caption-for x="short one"   # pick platforms
vauto post 1.jpg 2.jpg 3.jpg -c "Which one? #autumn" --audio track.mp3           # photos
vauto post clip.mp4 -c "..." --rewrite-captions                                  # Claude tailors captions
```

| Command | What it does |
|---|---|
| `vauto doctor [--online]` | What's set up, what's missing, how to fix it |
| `vauto platforms [--ready]` | Every platform with its route and status |
| `vauto jobs [--cancel ID] [--retry ID]` | Recent posts and the queue |
| `vauto trials` | Trial Reel results |
| `vauto worker [--once]` | Posts queued jobs when due |
| `vauto best-time` | The next best posting slot |
| `vauto auth meta / tiktok / refresh / status` | Connect accounts, manage tokens |
| `vauto browser login <platform>` | Save a login for Rutube, Likee, Dzen or Naver Clip |
| `vauto variant clip.mp4 -o preview.mp4` | Preview a Trial Reel version locally |
| `vauto accounts` | Accounts connected in Zernio |
| `vauto phone` | Your exact values for using vauto from your phone |

### With Claude Code

Drop a video into a Claude Code session in this repository and say *"post this everywhere with the caption '…', and do a trial reel"*. Claude runs a preview first, shows you the plan, and posts after you say go.

---

## Trial Reels

A Trial Reel is shown only to non-followers at first. Instagram requires a public professional account with 1,000+ followers.

- **Different enough to count as new.** Instagram demotes near-duplicates, so the trial version is zoomed (`--trial-zoom`, default 1.15×) or slowly pushes in (`--trial-mode push`). You can add hook text (`--trial-hook`), mirror it (`--trial-mirror`) or speed it up slightly (`--trial-speed 1.03`). It also gets a different cover frame.
- **Timing.** It posts at a random point in `--trial-delay 60-120` minutes after the main Reel, and only if the main Reel went out.
- **Graduation.** `MANUAL` (default) keeps it away from your followers until you share it in the app. `SS_PERFORMANCE` lets Instagram share it automatically if it does well.
- **Results.** 72 hours later vauto compares views, reach, likes, comments, shares and saves, picks a winner, and messages you on Telegram. See them any time with `vauto trials` or the Trial results tab.

---

## Scheduling and best times

`--at` (or **When** in the web app, **⏰** in the bot) accepts `now`, `best`, `18:30`, `6pm`, `tomorrow 9am`, `+2h`, `+90m` or `2026-10-01 18:30`. Times use `VAUTO_TIMEZONE` (default Europe/London).

`best` picks the next slot from `VAUTO_BEST_TIMES`. The default is UK engagement peaks: weekdays 07:30, 12:30, 18:00, 20:30 and weekends 10:00, 19:30. With `ZERNIO_PROFILE_ID` and Zernio analytics, it uses your own audience's best hours instead.

Posts routed through Zernio, and Chinese platforms, are scheduled on the platform's side. Direct Meta posts wait in vauto's local queue, so `vauto worker` must be running at that time (see below).

---

## Subtitles and music

**Subtitles.** `--subtitles auto` transcribes the speech on your computer with faster-whisper (`pip install 'vauto[subtitles]'`; the model downloads on first use) and burns in short, bold 2–4 word captions. The transcript is saved as `subtitles.srt` in the render folder: fix any word and post again. Or pass your own file: `--subtitles captions.srt`. `--subtitle-style clean` gives smaller captions.

**Music.** No platform lets an API add songs from its music library, so the audio in your file is what gets posted. Bake music in before posting, use `--tiktok-draft` to add a trending TikTok sound in the app, or post photos to TikTok, which adds a trending sound automatically.

---

## Phone-only apps, web uploaders and China

**Phone-only apps** (Lemon8, Kwai, Moj, Josh and others) have no way to post from a computer. vauto prepares the right version and sends the file and caption to your Telegram, so posting takes a few taps: save the video, open the app, paste the caption. Everything is also saved in `~/.vauto/outbox/`.

**Web uploaders** (Rutube, Likee, Dzen, Naver Clip) are driven like a person would, in a browser that keeps your login:

```bash
vauto browser login rutube     # log in once in the window that opens
vauto browser check rutube     # confirm the upload page is reachable
```

These sites change their pages without notice. If a step fails, vauto saves a screenshot in `~/.vauto/browser/debug/` and sends the post to your phone instead. The steps live in `videoautomation/config/browser_flows.yaml`; copy it, adjust a button label or selector, and point `VAUTO_BROWSER_FLOWS` at your copy.

**China** (Douyin, Kuaishou, Xiaohongshu, WeChat Channels, Bilibili, Weibo, Baijiahao) goes through the open-source [social-auto-upload](https://github.com/dreammis/social-auto-upload). Each account needs a Chinese phone number and ID verification.

```bash
git clone https://github.com/dreammis/social-auto-upload && cd social-auto-upload
pip install -e . && patchright install chromium
sau douyin login --account default      # scan the QR code with the Douyin app
```

---

## Keeping it running

Posts that wait for later on vauto's side (direct-Meta scheduled posts and Trial Reels, and the 72-hour results check) need `vauto worker` running at that time.

- With Docker, the worker is already running (see [Run it 24/7 with Docker](#run-it-247-with-docker)).
- On a computer that stays on: `vauto worker`, or a cron entry:
  ```
  */5 * * * * cd /path/to/VideoAutomation && vauto worker --once
  ```
- For the bot: run `vauto bot` on the same always-on machine.
- In a Claude Code cloud session, the machine is temporary. Connect Instagram in Zernio so Trial Reels schedule on Zernio's side.

`vauto doctor` warns when queued jobs are overdue because no worker ran.

**Cloud sessions and networks.** The machine vauto runs on must be allowed to reach the platform APIs (graph.facebook.com, zernio.com, open.tiktokapis.com, api.telegram.org, bsky.social and so on). Some sandboxed environments block these; run vauto on your own computer, or allow those hosts.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| A platform shows "needs setup" | `vauto doctor` names the missing setting |
| "needs media at a public URL" | Set `ZERNIO_API_KEY` or an S3/R2 bucket |
| Video too long for Facebook/Snapchat | Add `--trim`, or that platform is skipped |
| Trial Reel never posted | The worker was not running, or connect Instagram in Zernio |
| Instagram "limit reached" | 100 API posts per 24 h; wait |
| Browser platform handed off to phone | The site changed; see the screenshot in `~/.vauto/browser/debug/` |
| Bot says file too big | Telegram bots download 20 MB max; share to the vauto app, or turn on the big-files server |
| Phone won't install the app or show "vauto" in Share | It needs an https address: use `tailscale serve` (see [Use it from your phone](#use-it-from-your-phone)); on iPhone use the Shortcut |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests render real media with ffmpeg, drive a fake upload site in Chromium, and use fake HTTP sessions for every platform API, so nothing is posted.

Code map: `pipeline.py` (plan and run a post), `media/` (ffmpeg), `captions.py`, `publishers/` (one file per route), `scheduler.py` (queue), `insights.py` (Trial results), `timing.py` (scheduling), `bot.py`, `web/`, `doctor.py`, `config/platforms.yaml` (every platform's limits and routes).
