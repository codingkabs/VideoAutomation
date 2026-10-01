"""Public URLs for rendered media.

Instagram photos and every Zernio-routed post need media at a public URL.
Backends: S3-compatible storage (Cloudflare R2, AWS S3), Zernio's own media
hosting, or a dry-run stub that uploads nothing.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Protocol

import requests

from .config import Settings
from .errors import ConfigError, PublishError


class Storage(Protocol):
    name: str

    def put(self, path: Path, key: str) -> str:
        """Upload ``path`` and return a URL platforms can fetch."""


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


class DryRunStorage:
    name = "dry-run"

    def put(self, path: Path, key: str) -> str:
        return f"https://dry-run.invalid/{key}"


class S3Storage:
    """S3-compatible bucket. With ``S3_PUBLIC_BASE_URL`` (an R2 public bucket or
    custom domain) URLs are permanent; otherwise they are presigned and expire."""

    name = "s3"

    def __init__(self, settings: Settings):
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise ConfigError("S3 storage needs boto3: pip install 'vauto[s3]'") from exc
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region or "auto",
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def put(self, path: Path, key: str) -> str:
        s = self.settings
        full_key = f"{s.s3_prefix.strip('/')}/{key}" if s.s3_prefix else key
        self.client.upload_file(str(path), s.s3_bucket, full_key, ExtraArgs={"ContentType": _content_type(path)})
        if s.s3_public_base_url:
            return f"{s.s3_public_base_url}/{full_key}"
        ttl = min(max(1, s.url_ttl_hours), 168) * 3600  # presigned URLs max out at 7 days
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": s.s3_bucket, "Key": full_key}, ExpiresIn=ttl
        )


class ZernioStorage:
    """Zernio presigned upload (files up to 5 GB)."""

    name = "zernio"

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        if not settings.zernio_api_key:
            raise ConfigError("ZERNIO_API_KEY is not set")
        self.base = settings.zernio_base_url
        self.session = session or requests.Session()
        self.headers = {"Authorization": f"Bearer {settings.zernio_api_key}"}

    def put(self, path: Path, key: str) -> str:
        ctype = _content_type(path)
        resp = self.session.post(
            f"{self.base}/media/presign",
            headers=self.headers,
            json={"filename": path.name, "contentType": ctype},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise PublishError(f"Zernio presign failed ({resp.status_code}): {resp.text[:300]}",
                               transient=resp.status_code >= 500 or resp.status_code == 429)
        data = resp.json()
        # The live API returns publicUrl; Zernio's docs show fileUrl. Accept either.
        upload_url = data.get("uploadUrl")
        file_url = data.get("publicUrl") or data.get("fileUrl") or data.get("url")
        if not upload_url or not file_url:
            raise PublishError(f"Unexpected Zernio upload response (fields: {', '.join(sorted(data)) or 'none'})")
        with path.open("rb") as fh:
            put = self.session.put(upload_url, data=fh, headers={"Content-Type": ctype}, timeout=600)
        if put.status_code >= 400:
            raise PublishError(f"Upload to Zernio storage failed ({put.status_code})", transient=True)
        return file_url


def make_storage(settings: Settings, dry_run: bool = False) -> Storage | None:
    """Pick a backend. Returns None when nothing is configured (fine as long as
    no job needs a public URL)."""
    if dry_run:
        return DryRunStorage()
    choice = settings.storage
    if choice == "none":
        return None
    if choice == "s3" or (choice == "auto" and settings.s3_bucket):
        if not settings.s3_bucket:
            raise ConfigError("VAUTO_STORAGE=s3 but S3_BUCKET is not set")
        return S3Storage(settings)
    if choice == "zernio" or (choice == "auto" and settings.zernio_api_key):
        return ZernioStorage(settings)
    if choice not in ("auto", "s3", "zernio", "none"):
        raise ConfigError(f"Unknown VAUTO_STORAGE {choice!r}")
    return None
