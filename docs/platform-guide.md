# How each platform works (for creators)

This is the creator's side of the research: how posts get seen on each platform, what length and posting times suit it, and how people earn there. The technical side (APIs, limits, routes) is in [platform-research.md](platform-research.md).

The same guide is in the app: open **Platforms** and tap one, or run `vauto platforms tiktok`. The data lives in `videoautomation/config/platform_guide.yaml`.

Researched September 2026. Platforms change their ranking often, and much of what is published about algorithms comes from marketing blogs. Treat exact numbers as rough and trust your own numbers in **Stats** over anyone's rules.

---

## 1. What works everywhere

**The first two seconds decide everything.** Every short-video feed (TikTok, Reels, Shorts, Spotlight) tests a new video on a small group, and the main signal is whether people keep watching or swipe away. Open with movement, the result, or a question. Don't open with a logo or "hey guys".

**Completion beats length.** A 12-second video people finish beats a 40-second one they abandon. Shorter is not automatically better, though: TikTok and Snapchat only pay for videos over a minute, and longer TikToks can collect more total views.

**Shares are worth more than likes.** Instagram has said DM sends count several times more than likes for reaching new people. X counts replies and reposts far above likes. TikTok weighs shares, saves and comments above likes.

**Original content wins; reposts lose.** Meta (Instagram and Facebook) cut reach for accounts that repost other people's clips, and for videos carrying another app's watermark. That is why vauto uploads a clean file to each platform and makes the Trial Reel a visibly different version (zoom, hook text, new cover) rather than a copy.

**Subtitles and on-screen text help.** Many people watch without sound, especially on Facebook and LinkedIn. `--subtitles auto` burns them in.

**Trending sounds can't be added by any tool.** Instagram, TikTok and YouTube keep their music libraries in the apps. For a trending sound, use TikTok drafts (vauto sends the video to your TikTok inbox; you add the sound and post) or add it in the Instagram app. For photo posts, vauto asks TikTok to add music automatically.

**Posting often helps, but not endlessly.** Consistency matters on LinkedIn, Snapchat and Dzen. X limits how many of your posts reach any one person per day. Instagram and TikTok cap API posting at 100 and about 15 a day. vauto warns you before you hit a cap.

---

## 2. When to post in the UK

The best time depends on your audience. Once you have 3+ posts with numbers, **Stats → When your posts do best** shows your own pattern. Until then, these are the published patterns for UK audiences:

| Platform | Good times (UK) | Notes |
|---|---|---|
| Instagram | Weekdays 07:00–09:00 and 18:00–21:00; Sunday evening | Metricool's 2026 study found 20:00 gets the most views |
| TikTok | 18:00–20:00 daily; 06:00–10:00 | Saturday is the weakest day across UK networks |
| YouTube Shorts | Wed–Fri 15:00–17:00, evenings | |
| Facebook | Weekdays 09:00–13:00 and 19:00–21:00 | |
| Snapchat | 19:00–22:00; after school for young audiences | |
| LinkedIn | Tue–Thu 08:00–10:00 | |
| X, Threads | Weekday mornings and lunchtime | |
| Pinterest | 20:00–23:00, weekends | |

Sprout Social's UK data puts the overall peak at **Thursday 16:00–23:00**, and Tuesday–Wednesday 11:00–18:00 as the best days.

vauto's **Next best time** option (`--at best`) uses `VAUTO_BEST_TIMES` (default: weekdays 07:30, 12:30, 18:00, 20:30; weekends 10:00, 19:30). If you connect Zernio's analytics, it uses the best times from your own accounts.

---

## 3. Who uses what in the UK

From Ofcom's *Online Nation 2025* report:

- UK adults spend **4 h 30 min** online a day (18–24s: 6 h 20 min).
- **YouTube** reaches 94% of adults, about 51 min a day.
- **Facebook and Messenger** reach 93% of adults, about 43 min a day. **WhatsApp** reaches 90%.
- **TikTok** is visited by 56% of online adults; 18–34s spend about 49 min a day on it.
- **Instagram**: about 20 min a day on average.
- **Snapchat**: children aged 8–14 spend about 45 min a day on it; with YouTube it makes up about half their online time.

What that means: TikTok, Reels and Shorts for reach; Facebook for over-30s; Snapchat for teens and young adults; YouTube to build a long-term channel.

---

## 4. The UK core five

### Instagram
- **How it finds viewers.** A new Reel goes to a small test group first, then wider if they watch and share it. Adam Mosseri has named **watch time, sends per reach and likes per reach** as the main signals. Original content gets more distribution; accounts that mostly repost are left out of recommendations.
- **Length.** 7–15 s for reach, 30–60 s for tutorials. Reels up to 3 minutes can be recommended to non-followers. Longer ones (up to 20 min) only reach followers.
- **Hashtags.** 5 per post since late 2025. vauto keeps the first five and drops the rest.
- **Trial Reels** go to non-followers first. Since February 2026 they can be scheduled. vauto posts a zoomed variant 1–2 hours after your main Reel and compares the two 72 hours later.
- **Earning.** Gifts, subscriptions, bonuses by invitation; for most UK creators, brand deals.

### TikTok
- **How it finds viewers.** Each video is tested on a small group, often including some followers, then widened on completion, rewatches, shares, saves and comments. Creator guides in 2026 report TikTok leaning more on how your own followers react before pushing to strangers, so an engaged audience matters more than a big one.
- **Search.** People search TikTok like Google. Say the topic out loud and put it in the caption.
- **Length.** 15–30 s gets the highest engagement rate; 1–3 min videos collect more total views and are the only ones that earn from Creator Rewards.
- **Earning.** Creator Rewards (UK eligible): 18+, 10k followers, 100k views in 30 days, original videos over 1 minute. Views from the UK, US and Germany pay more. Also TikTok Shop affiliate, LIVE gifts and subscriptions.

### YouTube Shorts
- **How it finds viewers.** Ranked mainly on how many viewers keep watching versus swipe away. Since March 2025 every play counts as a view, but payouts use "engaged views".
- **Length.** 20–60 s works best; Shorts can be up to 3 minutes. Loops (an ending that flows into the start) help.
- **Earning.** YouTube Partner Programme: 1,000 subscribers plus 10M Shorts views in 90 days (or 4,000 watch hours). Creators get 45% of their share of the Shorts ad pool.

### Facebook
- **How it finds viewers.** The feed is now mostly recommended content, led by Reels. Meta boosts original videos and cuts reach for reposts and watermarked clips (policy update March 2026).
- **Length.** 15–60 s. vauto's direct route posts Reels up to 90 s.
- **Earning.** Facebook Content Monetization replaced the old programmes in August 2025 and is available in the UK. It covers ads on Reels and videos, performance bonuses, Stars and subscriptions, and is invitation-based.

### Snapchat
- **How it finds viewers.** Spotlight ranks by view time, completion, shares and favourites. Saved Stories on a Public Profile reach subscribers.
- **Length.** 15–30 s for reach. Payouts need Spotlights over 1 minute; vauto's Zernio route is set to 60 s until that limit is confirmed.
- **Earning.** Snapchat Monetization Programme (since February 2025): 50k followers, 25 posts a month, ads in Stories and in Spotlights over 1 minute. From May 2026 top rewards also need 100 hours of Spotlight view time in 28 days.

---

## 5. Other global platforms

- **Threads**: 500M+ monthly users (June 2026). Ranked on replies, reposts and your connection to the author. Videos under a minute and conversation starters work best.
- **X**: a Grok-based ranking model, whose code X published in January 2026. The first 30–60 minutes matter; a reply you answer counts far more than a like. There is a per-account cap on how many of your posts reach one person per day, so posting more often doesn't mean more reach. Posting through the API is paid.
- **LinkedIn**: ranks on dwell time; vertical video gets a boost. Use 30–90 s clips for discovery and 2–5 min for people who already follow you. Add subtitles.
- **Pinterest**: works like a search engine; keywords in the title and description decide reach. Short (6–15 s) videos that make the point without sound do best, and Pins keep getting views for months.
- **Bluesky**: about 46M registered users (August 2026). There is no single algorithm: Following is chronological, Discover is algorithmic, and 50,000+ custom feeds exist, often built on hashtags.
- **Telegram, WhatsApp Channels, Discord**: no algorithm. Every subscriber gets the post, so use them to tell your core fans a new video is out.
- **Reddit**: each subreddit has its own rules and moderators. Upvotes in the first hour decide whether a post rises.

---

## 6. Regional platforms

- **Russia**: YouTube is slowed there. VK Video became the most-used video service in 2026 (about 42M daily users in the first half), with Rutube second (about 85M monthly). Dzen, Yappy and OK.ru are smaller. You need Russian-language content.
- **India**: TikTok is banned. Instagram Reels and YouTube Shorts lead, followed by home-grown apps: ShareChat (350M+ users), Moj (160M+), Josh (150M+) and Chingari (50M+). About 70% of their users live outside big cities, and regional-language content (Hindi, Tamil, Telugu, Bhojpuri…) faces far less competition.
- **Brazil, Indonesia, Pakistan**: Kwai (Kuaishou's international app; called Snack Video in Pakistan and Indonesia).
- **Japan, Thailand, Taiwan**: LINE VOOM, inside the LINE messenger. **South Korea**: Naver Clip, inside the Naver app. **Vietnam**: Zalo.
- **Lemon8**: ByteDance's lifestyle app (Instagram meets Pinterest), strong on photo carousels. Its US operations have been under the TikTok USDS joint venture since January 2026, and it is available in the UK.

For India and China you need local phone numbers, and in China a real-name ID, for each account.

---

## 7. Mainland China

| App | Size | How it works |
|---|---|---|
| Douyin | ~755M users, 2+ h a day | Tests each video on a small pool, then widens it. Saves, revisits and follows now weigh more than completion alone |
| Kuaishou | ~736M monthly | Grassroots and everyday content; views are spread more evenly, so new creators get a fairer start |
| WeChat Channels | ~813M monthly | Spreads through friends' likes as well as the algorithm; watch-through and comments among contacts matter |
| Xiaohongshu (RedNote) | lifestyle search | Notes are found by search and topic; saves are the key signal; honest reviews with strong covers |
| Bilibili | young, loyal | Longer videos (3–15 min); "triple likes" (like, coin, favourite) and on-screen comments |
| Weibo | news, celebrities | Hot-search lists and #topic# hashtags |
| Baijiahao | Baidu | Search-led: titles and keywords decide reach |

---

## 8. What vauto does with this

- **Formats each post per platform**: length caps, 9:16 video, photo sizes, and slideshows for video-only platforms.
- **Fits captions**: Instagram's 5-hashtag cap, YouTube titles, character limits. With an Anthropic key, Claude can rewrite the caption for each platform's style.
- **Trial Reels**: a visibly different version for non-followers, then a winner check after 72 hours.
- **TikTok drafts** for trending sounds; automatic music for TikTok photo posts.
- **Scheduling** at your best times.
- **My posts and Stats**: your own numbers, best hours and best hashtags, so you can stop relying on generic advice.

---

## Sources

- Ofcom, [Online Nation 2025](https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/online-research/online-nation/2025/online-nations-report-2025.pdf?v=409837) and [how the UK goes online in 2025](https://www.ofcom.org.uk/media-use-and-attitudes/online-habits/from-apps-to-ai-search-how-the-uk-goes-online-in-2025)
- Instagram ranking: [Dataslayer](https://www.dataslayer.ai/blog/instagram-algorithm-2025-complete-guide-for-marketers), [Blck Alpaca](https://blckalpaca.at/en/knowledge-base/social-media/social-media-algorithms-distribution/instagram-algorithm-2026); updates: [SocialBee](https://socialbee.com/blog/instagram-updates/), [Social Media Examiner on the hashtag limit](https://www.socialmediaexaminer.com/what-clickable-reels-links-and-hashtag-limits-mean-for-your-2026-instagram-strategy/), [Later](https://later.com/blog/ultimate-guide-to-using-instagram-hashtags/)
- TikTok: [Hootsuite](https://blog.hootsuite.com/tiktok-algorithm/), [SocialPilot](https://www.socialpilot.co/blog/tiktok-algorithm), [TikTok Creator Rewards](https://www.tiktok.com/creator-academy/article/creator-rewards-program)
- YouTube: [Shorts monetization policies](https://support.google.com/youtube/answer/12504220?hl=en), [Metricool on the Shorts algorithm](https://metricool.com/youtube-shorts-algorithm/), [Gyre on the view-count change](https://gyre.pro/blog/youtube-shorts-view-count-update-impact-strategy-what-to-do-next)
- Facebook: [Meta, Rewarding original creators (March 2026)](https://about.fb.com/news/2026/03/rewarding-original-creators-on-facebook/), [Facebook Content Monetization](https://creators.facebook.com/tools/facebook-content-monetization)
- Snapchat: [Snap newsroom, unified monetization](https://newsroom.snap.com/snapchat-new-creator-monetization), [Snapchat help, Monetisation Programme](https://help.snapchat.com/hc/en-gb/articles/14669003687444-About-Snapchat-s-Monetisation-Programme)
- Threads: [Sprout Social statistics](https://sproutsocial.com/insights/threads-statistics/), [Metricool](https://metricool.com/threads-algorithm/)
- X: [Social Media Today on the Grok ranking](https://www.socialmediatoday.com/news/x-formerly-twitter-switching-to-fully-ai-powered-grok-algorithm/803174/), [Sprout Social](https://sproutsocial.com/insights/twitter-algorithm/)
- LinkedIn: [Hootsuite](https://blog.hootsuite.com/linkedin-algorithm/), [AuthoredUp](https://authoredup.com/blog/linkedin-video-posts)
- Pinterest: [84Pins](https://84pins.com/pinterest-video-pin-specs/), [b2w](https://www.b2w.tv/blog/pinterest-video-strategy)
- Bluesky: [TechCrunch 2026 roadmap](https://techcrunch.com/2026/01/27/bluesky-teases-2026-roadmap-a-better-discover-feed-real-time-features-and-more/), [Sprout Social](https://sproutsocial.com/insights/bluesky-statistics/)
- Best times: [Sprout Social UK](https://sproutsocial.com/insights/best-times-to-post-on-social-media-uk/), [Metricool](https://metricool.com/best-time-to-post-social-networks/), [Buffer](https://buffer.com/resources/best-time-to-post-social-media/), [Shopify UK](https://www.shopify.com/uk/blog/best-times-post-social-media)
- Video length: [Joyspace data study](https://joyspace.ai/ideal-video-length-social-platform-2026), [Fastlane benchmarks](https://www.usefastlane.ai/blog/short-form-video-benchmarks)
- Russia: [rb.ru on VK Video 2026](https://rb.ru/news/vk-video-stal-samym-populyarnym-videoservisom-v-rossii-v-2026-godu-obognal-youtube-i-rutube/), [Vedomosti](https://www.vedomosti.ru/media/news/2026/05/20/1198564-vk-i-rutube), [ETC Agency](https://blog.etc.moscow/en/a/youtube-rossiya-2026)
- India: [IdentityKit](https://www.identitykit.in/blog/sharechat-moj-josh-chingari-creators-india-2026), [Storyboard18](https://www.storyboard18.com/how-it-works/short-video-platforms-adex-to-surge-by-30-advertisers-split-over-homegrown-apps-effectiveness-58643.htm)
- Lemon8: [Metricool](https://metricool.com/lemon-8/), [TikTok USDS](https://en.wikipedia.org/wiki/TikTok_USDS)
- China: [DCH on Douyin, XHS and Channels algorithms](https://www.dchbi.com/post/douyin-vs-xiaohongshu-vs-wechat-channels-decoding-the-2026-algorithms-for-cross-border-growth), [ChoZan](https://chozan.co/chinese-social-media-platforms/)
