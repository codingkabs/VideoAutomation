# VideoAutomation

`vauto` posts one video or photo set to Instagram, Facebook, TikTok, YouTube Shorts and Snapchat. See README.md for setup and docs/platform-research.md for the platform research.

## When the user hands over media and a caption

1. Make sure tools are installed: `ffmpeg -version` and `vauto --help`. If missing, run `apt-get install -y ffmpeg` (or the OS equivalent) and `pip install -e ".[all]"`.
2. Run a dry run first and show the user the plan:
   `vauto post <files> -c "<caption>" [--trial] [--tiktok-draft] --dry-run`
3. If the plan looks right, run the same command without `--dry-run`.
4. Report each platform's link or error from the output. Say plainly which platforms failed and why.

Flags worth knowing:
- `--trial` adds the delayed, zoomed Instagram Trial Reel. `--trial-hook "text"` adds hook text.
- `--tiktok-draft` sends TikTok to drafts so the user can add a trending sound.
- `--trim` cuts videos to each platform's maximum length instead of skipping that platform.
- `--caption-for tiktok="..."` sets a per-platform caption. You can write these yourself when the user asks for tailored captions.
- `-p instagram,tiktok` limits platforms.

Never post without the user's go-ahead on the caption and platform list. A dry run is always safe.

## Development

- Tests: `pytest` (needs ffmpeg; publishers are tested with fake HTTP sessions).
- Platform limits live in `videoautomation/config/platforms.yaml`; update them there, not in code.
- New platforms: add a publisher in `videoautomation/publishers/`, register it in `make_publisher`, and mark the platform `status: implemented` in the YAML.
