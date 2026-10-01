# vauto: post once, publish everywhere

Give `vauto` one video (or a set of photos) and a caption. It formats the media for each platform, fits the caption to each platform's rules, and publishes to every platform you pick: Instagram, Facebook, TikTok, YouTube Shorts, Snapchat and about 40 more.

- **Instagram Trial Reels.** A zoomed-in version of your video goes out 1–2 hours later to non-followers, and vauto tells you 72 hours later which version won.
- **Made for phone editors:** finish your edit in CapCut or your gallery, tap **Share → vauto**, write the caption, post. It also works as a web app on your computer, a Telegram bot, or the command line (which Claude Code can drive for you).
- **My posts and Stats.** Every post is tracked with its links and its views, likes, comments and shares from each platform. You also see your best posting hours, the hashtags that work for you, and a posting calendar.
- **Subtitles, scheduling, best posting times, TikTok drafts for trending sounds, saved captions and per-platform captions** are built in.

The research behind it covers about 50 platforms:
- [docs/platform-guide.md](docs/platform-guide.md): how each platform works for creators (ranking, length, UK posting times, earning).
- [docs/platform-research.md](docs/platform-research.md): how each platform can be posted to automatically.

---

## Contents

1. [What it posts where](#what-it-posts-where)
2. [Post your first video](#post-your-first-video) · [Use it from your phone](#use-it-from-your-phone)
3. [Install](#install)
4. [Run it 24/7 with Docker](#run-it-247-with-docker)
5. [Setup, step by step](#setup-step-by-step)
6. [Using it](#using-it)
7. [My posts and Stats](#my-posts-and-stats) · [Drafts](#drafts) · [First comment](#first-comment)
8. [Trial Reels](#trial-reels)
9. [Scheduling and best times](#scheduling-and-best-times)
10. [Subtitles and music](#subtitles-and-music)
11. [Phone-only apps, web uploaders and China](#phone-only-apps-web-uploaders-and-china)
12. [Keeping it running](#keeping-it-running)
13. [Troubleshooting](#troubleshooting)

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

## Post your first video

The quickest path takes about 20 minutes on a Mac or Windows PC.

1. **Accounts.** Sign up at zernio.com. Connect Instagram, your Facebook Page, TikTok, YouTube and Snapchat (a few taps each, like logging in), then create an API key.
2. **Install.** Install [Python 3.10+](https://www.python.org/downloads/) and ffmpeg (`brew install ffmpeg` on Mac, `winget install Gyan.FFmpeg` on Windows, then open a new terminal). Then:
   ```bash
   cd VideoAutomation
   pip install -e ".[web]"
   cp .env.example .env            # Windows: copy .env.example .env
   ```
3. **Connect.** Put your key in `.env` as `ZERNIO_API_KEY=...`. Run `vauto accounts` and paste the `ZERNIO_ACCOUNT_...` lines it prints into `.env`. `vauto doctor` should now show the five platforms as ready.
4. **Test first.** Post to one platform, privately if you can:
   ```bash
   vauto post myvideo.mp4 -c "Test post #test" -p youtube --dry-run   # preview: nothing is posted
   vauto post myvideo.mp4 -c "Test post #test" -p tiktok --tiktok-draft
   ```
   The TikTok one lands in your TikTok drafts, so nothing goes public. When that works, post everywhere with `vauto post myvideo.mp4 -c "your caption"` or from the web app (`vauto web`).
5. **From your phone.** To share videos straight from your phone's editing app, see [Use it from your phone](#use-it-from-your-phone).

---

## Use it from your phone

If you edit on your phone, this is the everyday flow:

1. Export your edit from CapCut, InShot or your gallery.
2. Tap **Share → vauto** (or share it to your vauto Telegram bot).
3. The vauto app opens with the video loaded. Write the caption, tick the platforms and options, tap **Preview**, then **Post**.

Your phone only sends the video; the work happens on a computer (or small server) that stays on. One-time setup:

**1. Keep vauto running somewhere.** A home computer that stays on, a Mac mini, or a small cloud server (about £4 a month) all work. The easiest way is Docker: see [Run it 24/7 with Docker](#run-it-247-with-docker).

**2. Set a password.** Set `VAUTO_WEB_PASSWORD` in `.env` (or in the Setup tab). vauto refuses to open to other devices without one. The app then shows its own sign-in page and remembers each device for about a year, including home-screen apps on iPhone, which can't show the browser's password pop-up. Changing the password signs every device out; `/logout` signs out one. The iPhone Shortcut and scripts send the password in an `Authorization: Basic` header instead.

**3. Give it a secure address your phone can reach.** Phones only install web apps and accept shares over https. The simplest free option is [Tailscale](https://tailscale.com):

- Install Tailscale on the computer and on your phone, and sign in to both with the same account.
- On the computer, run `tailscale serve --bg 8765`. It prints an address like `https://your-pc.tail1234.ts.net` (the first time, it asks you to turn on HTTPS for your Tailscale network; say yes).
- Keep Tailscale switched on in the phone app whenever you use vauto.
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
# macOS: brew install ffmpeg      Ubuntu/Debian: sudo apt install ffmpeg      Windows: winget install Gyan.FFmpeg
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

**Quickest start:** connect Instagram and your Facebook Page in Zernio too. vauto then posts them through Zernio automatically, so you can skip step 2 below. That's about $18 a month for the UK five (2 free + 3 × $6). Step 2 is free but takes longer to set up. Once it's done, vauto switches Instagram and Facebook to the direct route by itself.

### 2. Instagram and Facebook

**Easiest:** connect them in Zernio too and add their `ZERNIO_ACCOUNT_...` ids. vauto uses Zernio for them automatically until you set up the direct route below.

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

- **Post:** drop a video or photos and write the caption. You get live length and hashtag checks per platform, and saved captions and hashtag sets you can add with one tap. Pick platforms and options, **Preview** to see every version with its caption and media, then **Post**.
- **My posts:** everything you posted, scheduled or still need to finish on your phone, with links and numbers per platform. You can search, retry, cancel, mark phone posts as done, **Post again** to more places, or export a CSV.
- **Stats:** views, likes, comments and shares, views by platform, top posts, your best posting hours, a posting calendar, the hashtags that work for you, and Trial Reel results.
- **Setup:** what's ready, and every setting.
- **Platforms:** all 50. Tap one to see how it works, what length and posting times suit it, how creators earn there, and what vauto needs.

On a phone the tabs sit at the bottom of the screen, and the back gesture moves between them.

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
vauto post clip.mp4 -c "..." --drafts                                            # save as drafts, post later
```

| Command | What it does |
|---|---|
| `vauto doctor [--online]` | What's set up, what's missing, how to fix it |
| `vauto platforms [--ready]` | Every platform with its route and status |
| `vauto platforms tiktok` | How one platform works, when to post, and what vauto needs |
| `vauto posts [ID] [--search text] [--show attention]` | Your post history with links and numbers |
| `vauto posts --mark-posted JOB --url LINK` | Record a phone hand-off you posted yourself |
| `vauto posts --publish POST_ID` | Post the drafts vauto is holding for that post |
| `vauto stats [--days 30] [--refresh]` | Views, likes, best platform, best hours and hashtags |
| `vauto export posts.csv` | Everything as a spreadsheet |
| `vauto clean [--dry-run]` | Delete old rendered videos to free disk space |
| `vauto jobs [--cancel ID] [--retry ID]` | Recent jobs and the queue |
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

## My posts and Stats

vauto records every real post (previews are not recorded): the caption, a thumbnail, each platform's link and status, and anything that was skipped and why.

**Where the numbers come from.** vauto reads views, likes, comments and shares from:
- Instagram and Facebook (direct, or Instagram posts made through Zernio when `IG_ACCESS_TOKEN` is set)
- Threads, Bluesky and Mastodon
- anything posted through Zernio (analytics are included with every Zernio account)

Phone hand-offs, browser uploads, Telegram and TikTok drafts can't report numbers. For those, tap **Mark as posted** and **Enter numbers** on My posts, or skip them.

**When they update.** The worker fetches fresh numbers every 6 hours for posts from the last 30 days (`VAUTO_STATS_REFRESH_HOURS`; 0 turns it off). You can also press **Update numbers** or run `vauto stats --refresh`. Posts that Zernio scheduled are checked too: once they are live, their link is saved.

**Weekly summary.** With Telegram notifications set up, the worker sends a summary every Monday at 9am: posts, views, your top platform and best post. Set `VAUTO_DIGEST=daily` or `off` to change it. In the bot, `/posts` and `/stats` answer any time.

**Daily limits.** vauto counts your recent posts and warns in the preview when you are close to a platform's cap (Instagram 100, Facebook Reels 30, TikTok about 15, Threads 250 a day).

**Your best times.** Once 3 or more posts have numbers, Stats shows which hours and hashtags do best for *you*. Trust that over any general advice, including [the guide](docs/platform-guide.md).

---

## First comment

Under the caption, **First comment** posts a comment on your own video straight after it goes up, on Instagram, Facebook, YouTube and LinkedIn. TikTok doesn't let apps post comments, so it's skipped there.

- **Write my own:** type it, or set a usual one in Setup (`VAUTO_FIRST_COMMENT=mine` and `VAUTO_FIRST_COMMENT_TEXT`).
- **Claude writes one:** Claude looks at a few frames of the video and your caption and writes one short comment that fits, like a question that gets replies. It appears in the box when you tap **Preview**, so you can edit it before posting. Needs an Anthropic API key in Setup → Extras (about a penny per comment).
- Command line: `--first-comment "text"`, `--first-comment claude` or `--first-comment off`.

The Trial Reel gets the same comment. With drafts, the comment goes on when you post the drafts.

---

## Drafts

Tick **Save as drafts** on the Post screen (or `--drafts`, or the 📝 button in the Telegram bot) to prepare everything without publishing:

| Platform | What happens |
|---|---|
| TikTok | Goes to your TikTok drafts (inbox). Add a sound and post it in TikTok |
| YouTube | Uploaded as **Private**. Set it to Public in the YouTube app when you're ready |
| Instagram, Facebook, LinkedIn and others | These platforms don't let any app save drafts, so vauto keeps them ready in **My posts**. Tap **Post drafts** to publish them all, or **Save video** to post one yourself from the app |

**Only some platforms as drafts.** Under the tick, choose which platforms to draft; the rest post straight away. For example, draft only Instagram so you can post it yourself from the Instagram app with **Share to Facebook** on. Instagram's combined Instagram + Facebook views only happen when you post from the app: no posting tool, Zernio or Meta's own API, can switch that on. In **My posts**, a held draft has **Share** (opens the platform's app with the video, or saves it to your camera roll), **Copy caption** and **Copy first comment**. After posting, tap **Mark as posted**. If you do this, untick Facebook in vauto so the video isn't posted twice. `VAUTO_DRAFT_PLATFORMS=instagram` makes that the default; on the command line, `--draft-only instagram`.

A Trial Reel in a draft post goes out 1–2 hours after you post the drafts. A scheduled time is ignored for drafts; you choose when by posting them. `VAUTO_DRAFTS_DEFAULT=true` ticks the box for every post. Drafts keep their videos until you post or discard them, however old they get.

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

**Disk space.** Every post renders a few versions of your video. The worker deletes rendered videos, uploads and hand-off files after 14 days (`VAUTO_KEEP_FILES_DAYS`; 0 keeps everything). Thumbnails, edited subtitles and anything still queued are kept. Run `vauto clean --dry-run` to see what would go, or `vauto clean` to do it now.

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
| iPhone video looks grey or washed out | It was HDR. vauto converts HDR to standard colour when ffmpeg has the `zscale` filter (Homebrew, Ubuntu and the Docker image do). Otherwise export SDR from your editing app |
| Photo comes out sideways | Update vauto; phone photos with a rotation flag are now handled |
| Stats show no views | Numbers come from Zernio, Instagram/Facebook direct, Threads, Bluesky or Mastodon; press Update numbers (they can take a few hours to appear). Phone hand-offs: type them in on My posts |
| Numbers never update | The worker must be running (Docker runs it); or press Update numbers |
| Phone won't install the app or show "vauto" in Share | It needs an https address: use `tailscale serve` (see [Use it from your phone](#use-it-from-your-phone)); on iPhone use the Shortcut |

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests render real media with ffmpeg, drive a fake upload site in Chromium, and use fake HTTP sessions for every platform API, so nothing is posted.

The web app is also tested in a real browser at phone size (tabs, My posts, Stats, Platforms, saved captions, and that every control has a name for screen readers).

Code map: `pipeline.py` (plan and run a post), `media/` (ffmpeg), `captions.py`, `publishers/` (one file per route), `scheduler.py` (queue), `tracker.py` (My posts, Stats, saved captions, weekly summary), `stats.py` (numbers from each platform), `insights.py` (Trial results), `timing.py` (scheduling), `bot.py`, `web/`, `doctor.py`, `config/platforms.yaml` (every platform's limits and routes), `config/platform_guide.yaml` (how each platform works).
