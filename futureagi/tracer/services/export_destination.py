"""Destination boundary for one-shot exports and future scheduled exports.

Paths are opaque object keys chosen by the job, never caller-supplied local paths.
write consumes bytes or a binary stream from its current position, synchronously;
it must finish before returning and must not close the caller's stream. url returns
an externally usable download URL. Only completed jobs expose it through the API.
"""
import ipaddress
import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit

from django.conf import settings


PRIVATE_EXPORT_PREFIX = "private-observe-exports/"
DOWNLOAD_TTL_SECONDS = 900


class ExportConfigurationError(ValueError):
    """A safe, operator-actionable error that may appear in job status."""


def validate_s3_endpoint(endpoint_url, region):
    """Admit exact operator-owned origins, not customer DNS preflight results."""
    if endpoint_url is None:
        return None
    error = "Custom S3 endpoint must be an HTTPS origin in the operator allowlist."
    try:
        parsed = urlsplit(endpoint_url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment
            or parsed.port == 0 or "\\" in endpoint_url
            or any(ord(char) <= 32 or ord(char) == 127 for char in endpoint_url)
        ):
            raise ValueError
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError
        origin = f"https://{parsed.netloc.lower()}"
        allowed = set(getattr(settings, "BULK_EXPORT_S3_ENDPOINT_ALLOWLIST", ()))
        if re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
            suffix = "amazonaws.com.cn" if region.startswith("cn-") else "amazonaws.com"
            allowed.add(f"https://s3.{region}.{suffix}")
        if origin not in allowed:
            raise ValueError
    except (TypeError, ValueError):
        raise ExportConfigurationError(error) from None
    return origin


class ExportDestination(ABC):
    @abstractmethod
    def write(self, path: str, content: bytes | BinaryIO) -> None:
        """Store content under path, raising on upload failure."""

    @abstractmethod
    def url(self, path: str) -> str:
        """Return a download URL for a successfully written object."""


class StorageExportDestination(ExportDestination):
    """One-shot exports never use the anonymously readable content bucket.

    This dedicated bucket is managed exclusively by exports. A deny on unsigned
    reads protects even an accidental public ACL; the general storage helper's
    public-bucket initialization must never be called here.
    """

    def _bucket(self, path):
        if not path.startswith(PRIVATE_EXPORT_PREFIX):
            raise ExportConfigurationError("Create a new private export to download this job.")
        if settings.STORAGE_BACKEND not in ("s3", "minio"):
            raise ExportConfigurationError("Configure S3 or MinIO private export storage before exporting.")
        return f"{settings.UPLOAD_BUCKET_NAME}-private-exports"

    def write(self, path: str, content: bytes | BinaryIO) -> None:
        from minio.error import S3Error
        from tfc.utils.storage_client import get_storage_client

        bucket = self._bucket(path)
        client = get_storage_client()
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Deny", "Principal": "*", "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"StringNotEquals": {"s3:signatureversion": "AWS4-HMAC-SHA256"}},
            }],
        }
        try:
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            else:
                try:
                    existing = json.loads(client.get_bucket_policy(bucket))
                    # MinIO canonicalizes wildcard principals on readback.
                    for statement in existing.get("Statement", []):
                        if statement.get("Principal") in ({"AWS": ["*"]}, {"AWS": "*"}):
                            statement["Principal"] = "*"
                except S3Error as exc:
                    if exc.code != "NoSuchBucketPolicy":
                        raise
                    existing = None
                # Never replace an operator's unrelated policy or adopt a
                # bucket with existing public grants. Fail before uploading.
                if existing is not None and existing != policy:
                    raise ValueError("Unexpected export bucket policy")
            client.set_bucket_policy(bucket, json.dumps(policy))
        except Exception:
            raise ExportConfigurationError(
                "Could not enforce private export storage. Configure the dedicated "
                "export bucket and permit its signed-read-only policy, then retry."
            ) from None
        stream = BytesIO(content) if isinstance(content, bytes) else content
        client.put_object(
            bucket_name=bucket, object_name=path, data=stream, length=-1,
            part_size=10 * 1024 * 1024,
            content_type="text/csv" if path.endswith(".csv") else "application/vnd.apache.parquet",
        )

    def url(self, path: str) -> str:
        from tfc.utils.storage_client import _parse_endpoint, get_storage_client

        bucket = self._bucket(path)
        if settings.STORAGE_BACKEND == "minio":
            from minio import Minio

            # Sign the browser-facing authority, never rewrite a signed URL's
            # host afterward. Region is explicit so signing makes no request
            # from the worker to a potentially browser-only endpoint.
            host, scheme_hint = _parse_endpoint(settings.MINIO_URL)
            # Match get_object_url: bare MinIO authorities default to HTTP.
            client = Minio(
                host, secure=bool(scheme_hint),
                access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", ""),
                secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
                region=os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1",
            )
        else:
            client = get_storage_client()
        return client.presigned_get_object(bucket, path, expires=timedelta(seconds=DOWNLOAD_TTL_SECONDS))


class FilesystemExportDestination(ExportDestination):
    """Network-free fake; its file URLs are intended only for tests."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, path: str) -> Path:
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise ValueError("Export path must be beneath the destination root")
        return target

    def write(self, path: str, content: bytes | BinaryIO) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        stream = BytesIO(content) if isinstance(content, bytes) else content
        with target.open("wb") as output:
            shutil.copyfileobj(stream, output, length=1024 * 1024)

    def url(self, path: str) -> str:
        return self._path(path).as_uri()


class _CallerOwnedStream:
    """boto3's managed upload may close its input; ownership stays with caller."""

    def __init__(self, stream):
        self.stream = stream

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def close(self):
        pass


class S3ExportDestination(ExportDestination):
    """Stream to a customer's bucket with explicit, decrypted credentials."""

    def __init__(self, *, bucket, prefix="", region, credentials, endpoint_url=None):
        import boto3
        from botocore.config import Config

        endpoint_url = validate_s3_endpoint(endpoint_url, region)
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.client = boto3.client(
            "s3", region_name=region, endpoint_url=endpoint_url,
            aws_access_key_id=credentials["aws_access_key_id"],
            aws_secret_access_key=credentials["aws_secret_access_key"],
            aws_session_token=credentials.get("aws_session_token"),
            config=Config(
                signature_version="s3v4", s3={"addressing_style": "path"},
                connect_timeout=10, read_timeout=60,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    def _key(self, path):
        return f"{self.prefix}/{path}" if self.prefix else path

    def write(self, path: str, content: bytes | BinaryIO) -> None:
        stream = BytesIO(content) if isinstance(content, bytes) else content
        self.client.upload_fileobj(
            _CallerOwnedStream(stream), self.bucket, self._key(path),
            ExtraArgs={"ContentType": "text/csv" if path.endswith(".csv") else "application/vnd.apache.parquet"},
        )

    def url(self, path: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": self._key(path)},
            ExpiresIn=900,
        )


def get_export_destination(job=None) -> ExportDestination:
    if job is not None and job.destination:
        from integrations.services.credentials import CredentialManager

        return S3ExportDestination(
            **job.destination,
            credentials=CredentialManager.decrypt(bytes(job.encrypted_credentials)),
        )
    return StorageExportDestination()
