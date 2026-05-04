"""
Configurable storage client factory.

Supports AWS S3 (default) and GCS (via S3-interop HMAC keys).
Controlled by STORAGE_BACKEND env var: "s3" or "gcs".

For S3 backend, the endpoint is configurable via S3_ENDPOINT env var.
Local dev points this at MinIO (e.g. S3_ENDPOINT=localhost:9005).
Production leaves it unset (defaults to s3.amazonaws.com).

GCS S3-interop: MinIO SDK talks to storage.googleapis.com using HMAC keys.
This avoids rewriting all upload/download logic — same MinIO API, different endpoint.
"""

import json
import os
from urllib.parse import urlparse

import structlog
from minio import Minio

logger = structlog.get_logger(__name__)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3")

# S3 endpoint — override for local MinIO (e.g. "minio:9000").
# Support both S3_ENDPOINT and S3_ENDPOINT_URL for compose/env compatibility.
_raw_s3_endpoint = os.getenv("S3_ENDPOINT") or os.getenv(
    "S3_ENDPOINT_URL", "s3.amazonaws.com"
)
_endpoint_secure_hint = None
if "://" in _raw_s3_endpoint:
    parsed = urlparse(_raw_s3_endpoint)
    _S3_ENDPOINT = parsed.netloc or parsed.path
    _endpoint_secure_hint = parsed.scheme == "https"
else:
    _S3_ENDPOINT = _raw_s3_endpoint
_S3_SECURE = (
    _S3_ENDPOINT == "s3.amazonaws.com"
    or os.getenv(
        "S3_SECURE",
        "true" if _endpoint_secure_hint is None else str(_endpoint_secure_hint).lower(),
    ).lower()
    == "true"
)

# Public URL host used when generating URLs returned to the browser.
# `S3_ENDPOINT_URL` may point at an internal Docker hostname
# (e.g. "http://minio:9000") which is unreachable from the user's
# browser. The existing `MINIO_URL` env var (read by tfc.settings) is
# the canonical browser-facing MinIO host — reuse it here so the two
# storage paths agree on what URLs they hand back to the frontend.
# Fall back to the internal endpoint when MINIO_URL isn't set (cloud
# prod with real S3 — same behaviour as before).
_raw_public_url = os.getenv("MINIO_URL") or _raw_s3_endpoint
_public_secure_hint = None
if "://" in _raw_public_url:
    _public_parsed = urlparse(_raw_public_url)
    _PUBLIC_HOST = _public_parsed.netloc or _public_parsed.path
    _public_secure_hint = _public_parsed.scheme == "https"
else:
    _PUBLIC_HOST = _raw_public_url
_PUBLIC_SECURE = (
    _PUBLIC_HOST == "s3.amazonaws.com"
    or (_public_secure_hint if _public_secure_hint is not None else _S3_SECURE)
)

_client = None


def get_storage_client() -> Minio:
    """Return a Minio client pointing at S3 (or MinIO) or GCS."""
    global _client
    if _client is not None:
        return _client

    if STORAGE_BACKEND == "gcs":
        _client = Minio(
            "storage.googleapis.com",
            access_key=os.getenv("GCS_HMAC_ACCESS_KEY", ""),
            secret_key=os.getenv("GCS_HMAC_SECRET_KEY", ""),
            secure=True,
        )
    else:
        # Prefer explicit S3_* credentials for storage (used by OSS MinIO setup),
        # then fall back to AWS_* for legacy/internal environments.
        access_key = os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", "")
        secret_key = os.getenv("S3_SECRET_KEY") or os.getenv(
            "AWS_SECRET_ACCESS_KEY", ""
        )
        region = os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION", "")
        _client = Minio(
            _S3_ENDPOINT,
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            secure=_S3_SECURE,
        )
    return _client


def reset_storage_client():
    """Reset the cached client. Useful in tests."""
    global _client
    _client = None


def get_object_url(bucket_name: str, object_key: str) -> str:
    """Build a browser-reachable URL for the given bucket/key.

    Uses MINIO_URL when set so URLs returned to the frontend resolve
    from the user's machine, not the internal Docker network."""
    if STORAGE_BACKEND == "gcs":
        return f"https://storage.googleapis.com/{bucket_name}/{object_key}"
    elif _PUBLIC_HOST != "s3.amazonaws.com":
        # Local MinIO or custom endpoint
        scheme = "https" if _PUBLIC_SECURE else "http"
        return f"{scheme}://{_PUBLIC_HOST}/{bucket_name}/{object_key}"
    else:
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-2")
        return f"https://{bucket_name}.s3.{region}.amazonaws.com/{object_key}"


def extract_object_key(file_url: str, bucket_name: str) -> str:
    """Extract the object key from a storage URL (S3, GCS, or MinIO)."""
    if "storage.googleapis.com" in file_url:
        # GCS format: https://storage.googleapis.com/{bucket}/{key}
        return file_url.split(f"{bucket_name}/", 1)[1]
    elif "amazonaws.com" in file_url:
        # S3 format: https://{bucket}.s3.{region}.amazonaws.com/{key}
        return file_url.split(".amazonaws.com/", 1)[1]
    else:
        # Local MinIO or custom endpoint: http://host:port/{bucket}/{key}
        return file_url.split(f"{bucket_name}/", 1)[1]


def ensure_bucket(client: Minio, bucket_name: str) -> None:
    """Create bucket with public policy if it doesn't exist. Policy only applies on S3."""
    if STORAGE_BACKEND == "gcs":
        # GCS buckets are pre-created via Terraform with IAM — skip
        return
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                }
            ],
        }
        client.set_bucket_policy(bucket_name, json.dumps(policy))
