# Short-form platform research (September 2026)

This document covers every short-form video platform worth posting to, UK first and then worldwide, and how each one can be automated. It is the research behind the `vauto` tool in this repository.

Numbers change often. Every spec here was checked in September 2026, and each one marked *(verify)* came from a single secondary source. The tool keeps all limits in `videoautomation/config/platforms.yaml` so they can be updated without code changes.

---

## 1. The short answer

- **Five platforms matter most in the UK.** Facebook (~36M UK users), Instagram (~30M), TikTok (~23M and the highest engagement), YouTube Shorts, and Snapchat Spotlight.
- **All five can be automated with official APIs**, but three have approval gates. TikTok and YouTube keep API uploads private until your app passes an audit, and Snapchat only lets allowlisted partners in.
- **A unified posting API gets you past those gates on day one.** Zernio, Ayrshare and Upload-Post are already approved partners. Zernio costs about $6 per connected account per month after two free accounts.
- **Instagram Trial Reels can be posted by API.** You add a `trial_params` field to a Reel, so the zoomed follow-up post can be fully automatic.
- **No platform lets an API add trending or library music.** Audio has to be baked into the video file. TikTok has two workarounds: auto-music on photo posts and sending the video to your drafts.
- **About 20 more platforms have official APIs**, including Threads, X, LinkedIn, Pinterest, Bluesky, Reddit, Telegram, VK, Dailymotion and Rumble.
- **The remaining ~25 have no posting API.** These include Lemon8, Likee, Kwai, Moj, Josh, Naver Clip and the Chinese apps. They can only be automated by driving a real browser, which breaks when the site changes and can breach the site's terms.

---

## 2. Three ways to post automatically

| Route | How it works | Good | Bad |
|---|---|---|---|
| **Official API** | Your own developer app, OAuth tokens, platform endpoints | Free, full feature access, stable | Audits for TikTok and YouTube, allowlist for Snapchat, one integration per platform |
| **Unified API** (Zernio, Ayrshare, Upload-Post) | One REST call, the service holds the approved apps | Public posting on day one, one integration, server-side scheduling | Monthly cost, limited to their ~15 platforms, a third party holds your tokens |
| **Browser automation** (Playwright) | A script drives a logged-in browser like a person would | Works on any site with a web uploader | Breaks on UI changes, may breach terms, account restriction risk, needs saved logins |

The tool combines all three. Official APIs are used where they are free and ungated (Instagram, Facebook). The unified API covers gated platforms (TikTok, YouTube, Snapchat). Browser automation is reserved for later, for platforms with nothing else.

---

## 3. UK core platforms in detail

### 3.1 Instagram (Reels, Trial Reels, carousels, Stories)

| Item | Detail |
|---|---|
| API | Instagram Graph API content publishing. Works with Facebook Login (`graph.facebook.com`) or Instagram Login (`graph.instagram.com`) |
| Account | Business or Creator (professional) account. Facebook Login also needs a linked Facebook Page |
| Gate | None for your own accounts. A dev-mode Meta app can post to accounts that hold a role on the app. App Review is only needed to serve other people |
| Flow | Create container → wait for `status_code=FINISHED` → `media_publish` → read `permalink` |
| Video upload | By public `video_url`, or **resumable upload** of the local file to `rupload.facebook.com/ig-api-upload/{version}/{container-id}` |
| Photo upload | Public `image_url` only, JPEG, 8 MB max, aspect ratio between 4:5 and 1.91:1 |
| Reels spec | MP4/MOV, H.264 or HEVC, AAC audio, 3 s to 15 min, 300 MB max via API. 9:16 recommended. Reels up to 3 min get full Reels distribution |
| Carousel | Up to **10** items through the API, mixed photo and video. The app allows 20 |
| Stories | `media_type=STORIES` with an image or video |
| Caption | 2,200 characters. **5 hashtags max** per post, enforced since December 2025 |
| Rate limit | 100 API-published posts per rolling 24 hours per account |
| Music | Library and trending music cannot be added by API. `audio_name` only renames your original audio |

**Trial Reels.** A Trial Reel is shown only to non-followers first. It never appears on your grid or in followers' feeds unless it "graduates". It needs a public professional account with 1,000+ followers.

To post one by API, add this to a normal Reels container:

```
trial_params={"graduation_strategy": "MANUAL"}
```

- `MANUAL` keeps it trial-only until you graduate it yourself in the app.
- `SS_PERFORMANCE` lets Instagram auto-graduate it if it performs well with non-followers in its first 72 hours.

**Originality rules (April 2026).** Instagram now stops recommending reposted or near-duplicate content to non-followers. Accounts with 10+ reposts in 30 days are excluded from recommendations entirely. This affects the trial-reel idea: the follow-up must be visibly different from the main Reel. The tool changes the framing (zoom or slow push-in), cover frame, optional hook text and optional mirror. It defaults to `MANUAL` graduation so a near-copy never lands in your followers' feed by accident.

### 3.2 Facebook (Page Reels, photos)

| Item | Detail |
|---|---|
| API | Graph API `/{page-id}/video_reels` (3 phases: start, upload, finish) |
| Account | **Facebook Pages only.** Personal profiles and Groups cannot be posted to by API |
| Gate | None for Pages you admin with a Page access token |
| Video upload | Local binary upload to `rupload.facebook.com/video-upload/{version}/{video-id}`, or a hosted `file_url` |
| Reels spec | 3–90 s, 24–60 fps, 9:16, min 540×960, under 1 GB |
| Rate limit | 30 API-published Reels per rolling 24 hours per Page |
| Photos | `/{page-id}/photos` for one image. Several images are uploaded unpublished, then attached to one `/{page-id}/feed` post |
| Note | Since June 2025 every Facebook video is a Reel |

### 3.3 TikTok (video, photo carousel, drafts)

| Item | Detail |
|---|---|
| API | Content Posting API. Direct Post: `/v2/post/publish/video/init/`. Photos: `/v2/post/publish/content/init/` |
| Gate | **Unaudited apps can only post privately.** The audit needs a public-facing app and a demo video. Reports say TikTok rejects tools that only post to the owner's own accounts |
| Drafts mode | `post_mode=MEDIA_UPLOAD` with the `video.upload` scope. The video lands in your TikTok inbox and you finish it in the app. **This needs no audit.** Limit of 5 pending drafts per 24 hours |
| Video spec | MP4/MOV/WebM, up to 10 min (per creator; read `max_video_post_duration_sec` from creator info), 4 GB max, 9:16 |
| Photo posts | Up to 35 images, JPEG or WebP, 20 MB each, pulled from a verified-domain URL. Title 90 characters, description 4,000 |
| Caption | 2,200 characters for video |
| Rate limit | About 15 posts per day per creator, shared by every app. 6 requests per minute per token |
| Required UX | Creator info must be fetched before posting. Privacy level, comments, duet and stitch settings must be set explicitly |
| Music | No sound picking for videos. Photo posts support `auto_add_music=true`, where TikTok picks a trending sound |

**Best way to get trending sounds:** send the video to drafts (`--tiktok-draft` in the tool), then open TikTok, add the sound and post. TikTok tends to favour in-app trending audio.

### 3.4 YouTube Shorts

| Item | Detail |
|---|---|
| API | YouTube Data API v3 `videos.insert`. There is no separate Shorts endpoint |
| Gate | Projects created after 28 July 2020 upload **private-only** until they pass a compliance audit. Setting `privacyStatus=public` does not bypass it |
| Short detection | Automatic for vertical or square videos up to 3 minutes. `#Shorts` in the title is optional but helps search |
| Quota | Since 1 June 2026 uploads have their own bucket: 100 `videos.insert` calls per day per project. Everything else shares 10,000 units |
| Title / description | Title 100 characters (about 40 show in the Shorts feed). Description 5,000. Tags 500 characters total |
| Photos | Not supported. The tool turns a photo set into a slideshow video |
| Music | No API. Content ID claims apply. Since 24 September 2026, Shorts of 1–3 min with a Content ID claim are no longer auto-blocked |

### 3.5 Snapchat (Spotlight, Saved Stories, Stories)

| Item | Detail |
|---|---|
| API | Public Profile API (Profile Asset Management). Create a media container, upload by multipart, then post |
| Gate | **Allowlist only.** You need a Business account, an OAuth app in Ads Manager, and approval from Snap. Ayrshare and Zernio already have access |
| Account | Snapchat Public Profile |
| Formats | Spotlight (permanent, entertainment feed), Saved Story (permanent on profile), Story (24 h) |
| Spec | One media item per post. Spotlight is 9:16 video, roughly 5–60 s *(verify)*. Video 500 MB max, image 20 MB max via Zernio |
| Text | Spotlight description 160 characters. Saved Story title 45 characters. Stories take no caption |
| Media | Zernio notes Snapchat requires AES-encrypted uploads, which the partner handles |

---

## 4. Photos versus video, per platform

| Platform | Single photo | Photo set | What the tool does with a photo set |
|---|---|---|---|
| Instagram | Yes | Carousel, max 10 by API | Carousel at 1080×1350 (4:5) |
| Facebook Page | Yes | Multi-photo post | Multi-photo feed post |
| TikTok | Yes | Photo post, max 35 | Photo post at 1080×1920, trending music auto-added |
| YouTube Shorts | No | No | Slideshow video |
| Snapchat | Yes, one item | No | Slideshow video to Spotlight |
| Threads, X, Bluesky | Yes | 10 / 4 / 4 images | Planned for Phase 2 |
| Pinterest | Yes | Carousel pins | Planned for Phase 2 |

---

## 5. Music and audio

No platform exposes its licensed music library to posting APIs. Anything posted by API uses the audio inside the file.

1. **Bake audio in before posting.** Use original audio, voiceover or royalty-free tracks. Copyrighted songs risk muting, blocking or Content ID claims.
2. **TikTok drafts.** Upload by API, then add a trending sound in the TikTok app.
3. **TikTok photo auto-music.** `auto_add_music=true` lets TikTok choose a trending sound.
4. **Instagram, YouTube, Facebook, Snapchat.** No API workaround. For a trending sound, post manually or add it in-app afterwards where editing allows.

The tool's `--audio` flag adds a soundtrack to photo slideshows.

---

## 6. Tier 2: other platforms with official APIs

| Platform | Region | Short-form surface | API route | Gate / cost | Key limits |
|---|---|---|---|---|---|
| Threads | Global | Video and image posts | Threads API (Meta), container then publish | None for own account | 250 posts / 24 h, video ≤5 min, 1 GB, public URL, 500 chars |
| X (Twitter) | Global | Video posts | X API v2 + media upload | Pay-per-use, about $0.015 per post, no free tier | 2 min 20 s video (non-Premium), 512 MB, 280 chars |
| LinkedIn | Global, B2B | Vertical video feed | Posts API | Self-serve `w_member_social` for your own profile | 15 min, 5 GB, ~100 calls/day per member |
| Pinterest | Global | Video Pins | API v5 `/media` then `/pins` | App review for production | Cover image required, title 100 chars |
| Bluesky | Global | Video posts | AT Protocol `app.bsky.video.uploadVideo` | Verified email | Up to 3 min / 100 MB widely supported; 10 min / 300 MB since Aug 2026 *(verify)*, 300 chars |
| Reddit | Global | Video posts in subreddits | Reddit API | OAuth app, subreddit rules | Varies per subreddit |
| Tumblr | Global | Video posts | API v2 (NPF) multipart | OAuth app | — |
| Telegram | Global, strong in RU/Middle East/Asia | Channel posts | Bot API `sendVideo` | Free | 50 MB (2 GB with a self-hosted Bot API server), 1,024-char media caption |
| Mastodon | Fediverse | Video posts | Mastodon API | Per instance | Instance-specific size limits |
| Discord | Global | Channel posts | Webhook | Free | 25 MB default upload |
| Vimeo | Global | Video hosting | Upload API | Paid plan for volume | — |
| Dailymotion | France / global | Videos, vertical player | Upload API | Free, mass upload on Pro Enterprise | — |
| Rumble | US / global | Videos | `rumble.com/api/simple-upload.php` | Token by emailing Rumble business development | — |
| VK Video / VK Clips | Russia, CIS | Clips (5 s–3 min vertical) | VK API `video.save` then upload URL | VK app token | 5,000 `video.save` calls/day |
| OK.ru | Russia, CIS | Video | OK API | App registration | — |
| PeerTube | Fediverse | Video | REST API | Per instance | — |
| Odysee | Global | Video | LBRY SDK / Odysee API | Crypto wallet setup | — |
| Google Business Profile | Local | Photos, posts | Business Profile API | Verified business | Images only via Zernio |

---

## 7. Tier 3: no public posting API (browser automation only)

| Platform | Main markets | Notes |
|---|---|---|
| Lemon8 | US, Japan, SE Asia | ByteDance. No posting API; only scrapers exist |
| Likee | Russia, SE Asia, Middle East | Web batch uploader exists; no API |
| Kwai | Brazil, Latin America, Indonesia | Only ads (marketing) API is public |
| Triller | US, India | Unofficial wrappers only |
| Moj | India | ShareChat group; Indian phone number needed |
| Josh | India | Indian phone number needed |
| Chingari | India | — |
| ShareChat | India | — |
| Roposo | India | — |
| Snack Video | Pakistan, Indonesia | Kuaishou-owned |
| Naver Clip | South Korea | Internal JSON API with cookie auth only; Korean verification for some features |
| LINE VOOM | Japan, Thailand, Taiwan | Posting through LINE VOOM Studio web UI for Official Accounts |
| Rutube | Russia | No public upload API; access by request |
| Dzen | Russia | Public API is read-only |
| Yappy | Russia | — |
| Zalo | Vietnam | — |
| Clapper | US | — |
| Fanbase | US | — |
| WhatsApp Channels | Global | Business API cannot post to Channels |
| Facebook Groups | Global | Group posting API removed in 2024 |

Browser automation risks:
- Sites change their upload pages, so each script needs maintenance.
- Automated posting can breach terms and may lead to rate limits or account restrictions. Use accounts you are prepared to have restricted, and post at human pace.
- Many regional apps require a local phone number to register.

---

## 8. Tier 4: mainland China

| Platform | Type |
|---|---|
| Douyin | Chinese TikTok |
| Kuaishou | Short video |
| Xiaohongshu (RedNote) | Lifestyle photo and video notes |
| WeChat Channels | Short video inside WeChat |
| Bilibili | Video, strong with young audiences |
| Weibo | Microblog with video |
| Baijiahao | Baidu content platform |

Each account needs a Chinese phone number and real-name ID verification. Official open platforms exist for Douyin and others, but posting access is restricted to registered Chinese entities. The open-source project `dreammis/social-auto-upload` (Python + Playwright) already automates video and photo uploads with scheduling for Douyin, Kuaishou, Xiaohongshu, WeChat Channels, Bilibili, Baijiahao and TikTok. It is the planned basis for Phase 4.

---

## 9. Unified posting APIs compared

| Service | Platforms | Price (2026) | Trial Reels | TikTok drafts | Snapchat | Scheduling |
|---|---|---|---|---|---|---|
| **Zernio** (formerly Late) | 15: IG, TikTok, X, FB, LinkedIn, YouTube, Threads, Pinterest, Reddit, Bluesky, Telegram, Google Business, Snapchat, WhatsApp, Discord | 2 accounts free, then $6/account (3–10), $3 (11–100) | Yes (`trialParams`) | Yes (`draft: true`) | Story, Saved Story, Spotlight | Yes (`scheduledFor`) |
| Ayrshare | 13 | $149/mo (1 profile) to $599/mo (30) | Yes (`trialParams`) | — | Yes | Yes |
| Upload-Post | ~10 | Cheaper at low volume, posting only | Yes (`share_mode`) | Yes | — | Yes |
| Postiz (open source) | 33, self-hosted | Free to self-host | — | — | — | Yes |

Zernio was chosen as the default: cheapest for a handful of accounts, covers all three gated platforms, supports Trial Reels, drafts and server-side scheduling, and hosts media uploads.

**Cost for the UK core five, all through Zernio:** 2 free + 3 × $6 = about **$18 per month**. With Instagram and Facebook posted directly through Meta (the tool's default), only TikTok, YouTube and Snapchat are connected: 2 free + 1 × $6 = about **$6 per month**.

---

## 10. How the automation works

```
video/photos + caption
        │
        ▼
 probe (ffprobe) ──► normalize to 1080×1920 H.264/AAC master
        │
        ├─► per-platform renditions (duration trims, size caps)
        ├─► per-platform captions (length, hashtag caps, titles)
        ├─► trial variant (zoom / push-in, new cover, hook text)
        │
        ▼
 publish now, in parallel:
   Instagram + Facebook ── Meta Graph API (direct, resumable upload)
   TikTok, YouTube, Snapchat ── Zernio API
        │
        ▼
 Instagram Trial Reel +60–120 min
   direct: local job queue (`vauto worker`)
   Zernio: server-side `scheduledFor`
        │
        ▼
 report: link or error for every platform
```

See the README for setup and commands.

---

## 11. What you need to set up by hand

1. Instagram professional account (Business or Creator), linked to a Facebook Page. 1,000+ followers for Trial Reels.
2. Facebook Page you admin.
3. Snapchat Public Profile.
4. A Zernio account with TikTok, YouTube and Snapchat connected, plus an API key.
5. A Meta developer app with Instagram and Pages permissions, and long-lived tokens. Or connect Instagram and Facebook in Zernio too and skip this step.
6. For photo posts to Instagram through Meta directly: public storage for images (Cloudflare R2 or S3), or Zernio's media hosting.

---

## 12. Sources

- Meta, Instagram content publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/
- Meta, Instagram resumable uploads: https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/
- Meta, Facebook Reels publishing: https://developers.facebook.com/docs/video-api/guides/reels-publishing/
- Ayrshare Instagram docs (trialParams): https://www.ayrshare.com/docs/apis/post/social-networks/instagram
- Trial params tested in production: https://github.com/JohnnyOliveirasp/lucasarrial/pull/407
- Instagram Trial Reels: https://creators.instagram.com/blog/instagram-trial-reels
- Instagram audio rules: https://help.instagram.com/329208821595430
- Instagram 5-hashtag limit: https://later.com/blog/ultimate-guide-to-using-instagram-hashtags/
- Instagram originality update: https://www.tubefilter.com/2026/04/30/instagram-removes-algorithm-recommendations-repost-content-aggregator/
- Instagram carousel limit: https://metricool.com/instagram-increases-carousel-content-limit/
- Facebook Reels limits: https://www.ayrshare.com/docs/media-guidelines/facebook_pages
- TikTok Direct Post: https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- TikTok Upload (drafts): https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content
- TikTok limits: https://zernio.com/blog/tiktok-posting-api
- TikTok drafts without audit: https://github.com/TysAIs/xPST/pull/266
- TikTok photo auto music: https://zernio.com/changelog/automatically-add-trending-music-to-tiktok-image-posts
- YouTube videos.insert: https://developers.google.com/youtube/v3/docs/videos/insert
- YouTube 2026 quota change: https://www.outstand.so/blog/youtube-api-pricing-quota
- YouTube 3-minute Shorts: https://support.google.com/youtube/answer/15424877
- Snapchat Public Profile API: https://developers.snap.com/marketing-api/Public-Profile-API/Introduction
- Ayrshare Snapchat: https://www.ayrshare.com/docs/apis/post/social-networks/snapchat
- X API pricing: https://docs.x.com/x-api/getting-started/pricing
- Threads API: https://postproxy.dev/blog/how-to-post-to-threads-via-api/
- Pinterest API v5: https://developers.pinterest.com/docs/api/v5/pins-create/
- LinkedIn posting: https://www.blotato.com/blog/linkedin-posting-api
- Bluesky video: https://docs.bsky.app/docs/tutorials/video
- Bluesky 2026 limits: https://useagentsky.com/blog/bluesky-video-limits
- Telegram Bot API limits: https://medium.com/@khudoyshukur/how-to-bypass-telegram-bot-50-mb-file-limit-3a4d9b1788ae
- VK video.save: https://vknet.github.io/vk/video/save/
- VK Clips: https://ads.vk.ru/en/insights/kak-rabotat-s-vk-klipami
- Dailymotion upload: https://developers.dailymotion.com/docs/upload-videos
- Rumble Upload API: https://player.rumble.com/developers/Rumble-Upload-API.html
- Rutube upload access: https://github.com/orgs/community/discussions/165690
- Zernio API skill (field names): https://github.com/zernio-dev/zernio-api
- Zernio pricing: https://zernio.com/pricing
- Ayrshare vs Zernio pricing: https://zernio.com/alternatives/ayrshare
- Postiz: https://github.com/gitroomhq/postiz-app
- social-auto-upload: https://github.com/dreammis/social-auto-upload
- UK usage: https://birdeye.com/blog/social-media-sites-uk/
