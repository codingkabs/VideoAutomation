"""Mainland China platforms through social-auto-upload (`sau`, MIT licensed).

social-auto-upload drives the creator websites of Douyin, Kuaishou,
Xiaohongshu, WeChat Channels, Bilibili, Weibo and Baijiahao with a real browser
and a saved login. Install it separately and log in once per platform:

    git clone https://github.com/dreammis/social-auto-upload && cd social-auto-upload
    pip install -e . && patchright install chromium
    sau douyin login --account default
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import timedelta
from zoneinfo import ZoneInfo

from ..config import platform_spec
from ..errors import ConfigError, PublishError
from ..models import PostJob, PostResult
from ..scheduler import parse_iso, utcnow
from .base import Publisher

CHINA_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 30 * 60
LOGIN_HINTS = ("login", "登录", "cookie", "扫码", "expired", "失效")


class SauPublisher(Publisher):
    name = "sau"

    def schedules_remotely(self, job: PostJob) -> bool:
        return bool(platform_spec(job.platform).get("sau_schedule"))

    def command(self, job: PostJob) -> list[str]:
        spec = platform_spec(job.platform)
        s = self.settings
        site = spec["sau_platform"]
        title = job.options.get("title") or job.caption[:20]
        tags = ",".join(job.options.get("tags") or [])
        base = [s.sau_command, site]
        if job.media[0].kind == "image":
            cmd = base + ["upload-note", "--account", s.sau_account, "--images", *[m.path for m in job.media],
                          "--title", title, "--note", job.caption]
        else:
            cmd = base + ["upload-video", "--account", s.sau_account, "--file", job.media[0].path,
                          "--title", title, "--desc", job.caption]
            if site == "bilibili":
                cmd += ["--tid", s.bilibili_tid]
        if tags:
            cmd += ["--tags", tags]
        if job.run_at and self.schedules_remotely(job) and parse_iso(job.run_at) > utcnow() + timedelta(minutes=5):
            cmd += ["--schedule", parse_iso(job.run_at).astimezone(CHINA_TZ).strftime("%Y-%m-%d %H:%M")]
        return cmd

    def publish(self, job: PostJob) -> PostResult:
        if not shutil.which(self.settings.sau_command):
            raise ConfigError(
                f"`{self.settings.sau_command}` (social-auto-upload) is not installed. See README > China platforms."
            )
        cmd = self.command(job)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        except subprocess.TimeoutExpired as exc:
            raise PublishError(f"{job.platform}: upload took longer than 30 minutes", transient=True) from exc
        output = (proc.stdout + "\n" + proc.stderr).strip()
        if proc.returncode != 0:
            tail = " | ".join(output.splitlines()[-3:])
            if any(h in output.lower() for h in LOGIN_HINTS):
                tail += f" (log in again: {self.settings.sau_command} {cmd[1]} login --account {self.settings.sau_account})"
            raise PublishError(f"{job.platform} upload failed: {tail}")
        scheduled = "--schedule" in cmd
        return PostResult(job.platform, job.surface, "scheduled" if scheduled else "published",
                          run_at=job.run_at if scheduled else None,
                          notes=["posted through social-auto-upload; check the app for the link"])
