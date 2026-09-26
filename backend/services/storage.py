"""Storage abstraction for slides, heatmaps, tile crops and result JSON.

Keys are random-UUID based (never user-supplied names), so there is no path traversal.
- LocalStorage: a folder outside the web root (STORAGE_DIR).
- S3Storage: S3/MinIO bucket; slides are copied to SLIDE_CACHE_DIR because OpenSlide
  needs a local file.
"""

from __future__ import annotations

import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from ..core.config import get_settings

_KEY_RE = re.compile(r"^[a-z0-9_-]+(/[a-zA-Z0-9_.-]+)*$")


def check_key(key: str) -> str:
    if not _KEY_RE.fullmatch(key) or ".." in key:
        raise ValueError(f"invalid storage key {key!r}")
    return key


class Storage(Protocol):
    def put_file(self, key: str, src: Path, content_type: str = "application/octet-stream") -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def local_path(self, key: str) -> Path: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...
    def ping(self) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / check_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("key escapes storage root")
        return p

    def put_file(self, key: str, src: Path, content_type: str = "application/octet-stream") -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dst)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def local_path(self, key: str) -> Path:
        p = self._p(key)
        if not p.exists():
            raise FileNotFoundError(key)
        return p

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str) -> None:
        self._p(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        p = self._p(prefix)
        if p.is_dir():
            shutil.rmtree(p)

    def ping(self) -> bool:
        return self.root.exists()


class S3Storage:
    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self.bucket = s.S3_BUCKET
        self.sse = {"ServerSideEncryption": s.S3_SSE} if s.S3_SSE else {}
        self.cache = s.SLIDE_CACHE_DIR
        self.cache.mkdir(parents=True, exist_ok=True)
        self.s3 = boto3.client(
            "s3",
            endpoint_url=s.S3_ENDPOINT_URL,
            region_name=s.S3_REGION,
            aws_access_key_id=s.S3_ACCESS_KEY.get_secret_value(),
            aws_secret_access_key=s.S3_SECRET_KEY.get_secret_value(),
        )

    def put_file(self, key: str, src: Path, content_type: str = "application/octet-stream") -> None:
        self.s3.upload_file(
            str(src),
            self.bucket,
            check_key(key),
            ExtraArgs={"ContentType": content_type, **self.sse},
        )
        cached = self.cache / key
        cached.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), cached)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, ContentType=content_type, **self.sse)

    def get_bytes(self, key: str) -> bytes:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=check_key(key))
        except self.s3.exceptions.NoSuchKey:
            raise FileNotFoundError(key) from None
        return obj["Body"].read()  # type: ignore[no-any-return]

    def local_path(self, key: str) -> Path:
        p = self.cache / check_key(key)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(p.suffix + ".part")
            self.s3.download_file(self.bucket, key, str(tmp))
            tmp.replace(p)
        return p

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=check_key(key))
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=check_key(key))
        (self.cache / key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=check_key(prefix) + "/"):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
        shutil.rmtree(self.cache / prefix, ignore_errors=True)

    def ping(self) -> bool:
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    return S3Storage() if s.STORAGE_BACKEND == "s3" else LocalStorage(s.STORAGE_DIR)


def case_prefix(case_id: str) -> str:
    return f"cases/{case_id}"
