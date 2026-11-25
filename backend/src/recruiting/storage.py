"""Document storage for original files (BRD section 11): the resumes candidates and recruiters
upload, kept as they were sent so recruiters can open them and clients receive the original.

One interface, one adapter per store, chosen by RAP_DOCUMENT_STORES (the first one listed):
- "s3": any S3-compatible store: AWS S3, MinIO (the local Podman stack), Cloudflare R2,
  Google Cloud Storage's S3 API. Credentials come from the secrets provider
  (s3_access_key_id, s3_secret_access_key) or, when those are not set, the standard AWS chain
  (instance role, environment).
- "memory": in-process, for tests and in-memory runs.
SharePoint and OneDrive adapters plug in behind the same interface (not built yet, doc 08).

Keys are opaque and carry no personal data: candidates/<candidate id>/<random id><extension>.
Each stored file records its SHA-256, so a file attached to a client email can be checked
against the one the recruiter approved.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any, Protocol

ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
}


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    key: str
    filename: str  # as uploaded, for the download and the email attachment
    content_type: str
    size: int
    sha256: str
    stored_at: str  # ISO time

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> StoredFile | None:
        return cls(**data) if data else None


class DocumentStore(Protocol):
    name: str

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> bytes:
        """The file's bytes; raises StorageError if it is not there."""
        ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int:
        """Delete every file under the prefix; the number deleted."""
        ...


def safe_filename(name: str | None) -> str:
    """The uploaded file's base name, without any path, kept short and printable."""
    base = PurePath((name or "").replace("\\", "/")).name.strip()
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '"<>|:*?')[:120]
    return cleaned or "resume"


def store_resume(
    store: DocumentStore, candidate_id: str, filename: str | None, data: bytes, kind: str
) -> StoredFile:
    """Keep an uploaded resume. `kind` is what documents.extract_text found from the content
    ("pdf", "docx" or "text"), not the file name, so the stored type is the real one."""
    extension = {"pdf": ".pdf", "docx": ".docx"}.get(kind, ".txt")
    key = f"candidates/{candidate_id}/{uuid.uuid4().hex}{extension}"
    content_type = ALLOWED_TYPES[extension]
    store.put(key, data, content_type)
    name = safe_filename(filename)
    if not name.lower().endswith(extension):
        name = f"{PurePath(name).stem or 'resume'}{extension}"
    return StoredFile(
        key, name, content_type, len(data), hashlib.sha256(data).hexdigest(),
        datetime.now(UTC).isoformat(),
    )  # fmt: skip


class InMemoryDocumentStore:
    name = "memory"

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = (bytes(data), content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.files[key][0]
        except KeyError:
            raise StorageError(f"No stored file {key}") from None

    def delete(self, key: str) -> None:
        self.files.pop(key, None)

    def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.files if k.startswith(prefix)]
        for k in keys:
            del self.files[k]
        return len(keys)


class S3DocumentStore:
    """Any S3-compatible store. The bucket is created on first use if it does not exist (local
    MinIO); in AWS it is normally created by the infrastructure module with encryption on."""

    name = "s3"

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        client: Any = None,
    ) -> None:
        if not bucket:
            raise ValueError("The S3 document store needs a bucket (RAP_S3_BUCKET)")
        self._bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                config=Config(s3={"addressing_style": "path"}, retries={"mode": "standard"}),
            )
        self._s3 = client
        self._ready = False

    def _ensure_bucket(self) -> None:
        if self._ready:
            return
        from botocore.exceptions import ClientError

        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                raise StorageError(f"Bucket {self._bucket} is not usable: {exc}") from exc
            self._s3.create_bucket(Bucket=self._bucket)
        self._ready = True

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._ensure_bucket()
        # AWS S3 encrypts every new object by default; MinIO encrypts when given a KMS.
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            return self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except ClientError as exc:
            raise StorageError(f"No stored file {key}") from exc

    def delete(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def delete_prefix(self, prefix: str) -> int:
        self._ensure_bucket()
        deleted = 0
        for page in self._s3.get_paginator("list_objects_v2").paginate(
            Bucket=self._bucket, Prefix=prefix
        ):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._s3.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
                deleted += len(keys)
        return deleted
