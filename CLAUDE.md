# VideoAutomation

`vauto` posts one video or photo set to Instagram, Facebook, TikTok, YouTube Shorts, Snapchat and about 40 more platforms (official APIs, Zernio, browser automation, phone hand-off, and social-auto-upload for China). See README.md for setup and docs/platform-research.md for the research.

## When the user hands over media and a caption

1. Make sure tools are installed: `ffmpeg -version` and `vauto --help`. If missing, run `apt-get install -y ffmpeg` (or the OS equivalent) and `pip install -e ".[all]"`.
2. Run `vauto doctor` if you are unsure what is set up.
3. Run a dry run first and show the user the plan:
   `vauto post <files> -c "<caption>" [--trial] [--tiktok-draft] [--at best] --dry-run`
4. If the plan looks right and the user agrees, run the same command without `--dry-run`.
5. Report each platform's link or error from the output. Say plainly which platforms failed and why.

Flags worth knowing:
- `-p instagram,tiktok,x` picks platforms (default: VAUTO_PLATFORMS). `vauto platforms` lists them all.
- `--trial` adds the delayed, zoomed Instagram Trial Reel; `--trial-hook "text"` adds hook text.
- `--tiktok-draft` sends TikTok to drafts so the user can add a trending sound.
- `--drafts` publishes nothing: TikTok drafts, YouTube private, everything else held in vauto until `vauto posts --publish POST_ID`.
- `--at best|18:30|tomorrow 9am|+2h` schedules the post.
- `--subtitles auto` or `--subtitles file.srt` burns in captions.
- `--trim` cuts videos to each platform's maximum length instead of skipping that platform.
- `--caption-for tiktok="..."` sets a per-platform caption. You can write these yourself when the user asks for tailored captions.

After posting, or when the user asks how things are going: `vauto posts` (history with links and numbers), `vauto stats [--refresh]` (totals, best platform, best hours and hashtags), `vauto platforms NAME` (how a platform works and when to post there; docs/platform-guide.md has the full guide). Hand-offs the user finished on their phone: `vauto posts --mark-posted JOB --url LINK`. Free disk space: `vauto clean`.

Instagram and Facebook fall back to Zernio automatically when their direct Meta settings are missing and a Zernio account is connected for them. The quickest setup is Zernio for all five UK platforms.

Never post without the user's go-ahead on the caption and platform list. A dry run is always safe.

If the user asks about posting from their phone, point them to README "Use it from your phone" and `vauto phone` (installable web app with Share → vauto, an iPhone Shortcut, or the Telegram bot; `docker compose up -d` keeps it running 24/7).

This session's machine may block the platform APIs (network policy) and is temporary, so anything queued locally (`vauto worker`) is lost when it ends. Prefer Zernio routes for scheduled posts here, or have the user run vauto on their own computer.

## Development

- Tests: `pytest` (needs ffmpeg; Chromium for the browser test; publishers use fake HTTP sessions).
- Platform limits and routes live in `videoautomation/config/platforms.yaml`; update them there, not in code. The creator guide per platform is `config/platform_guide.yaml` (a test checks every platform has one).
- New platform: add it to the YAML (`status`, `backends`, `surfaces`, limits). A new route means a publisher in `videoautomation/publishers/`, registered in `BACKENDS` in `publishers/__init__.py`, plus a readiness rule in `doctor.platform_ready`.
- Every setting is listed in `videoautomation/fields.py`; `.env.example` must contain each key (a test checks this).
