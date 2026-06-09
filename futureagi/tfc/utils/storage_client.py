"""
Configurable storage client factory.

STORAGE_BACKEND selects the backend and is the single source of truth for
URL routing:

  s3     AWS S3. URLs use the virtual-hosted bucket form.
  gcs    GCS via S3-interop HMAC keys. URLs use storage.googleapis.com.
  minio  Self-hosted MinIO (OSS local stack). The backend client talks to
         the internal endpoint (S3_ENDPOINT_URL, e.g. http://minio:9000)
         while browser-facing URLs use MINIO_URL (e.g. http://localhost:9005).
"""

import json
import os
from urllib.parse import urlparse

import structlog
from minio import Minio

logger = structlog.get_logger(__name__)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3").lower()
_client = None


def _parse_endpoint(raw_endpoint: str) -> tuple[str, bool | None]:
    if "://" in raw_endpoint:
        parsed = urlparse(raw_endpoint)
        return parsed.netloc or parsed.path, parsed.scheme == "https"
    return raw_endpoint, None


def get_storage_client() -> Minio:
    """Return a Minio client pointing at S3, MinIO, or GCS."""
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
        return _client

    raw_endpoint = os.getenv("S3_ENDPOINT") or os.getenv(
        "S3_ENDPOINT_URL", "s3.amazonaws.com"
    )
    host, scheme_hint = _parse_endpoint(raw_endpoint)

    secure_env = os.getenv("S3_SECURE")
    if secure_env is not None:
        secure = secure_env.lower() == "true"
    elif scheme_hint is not None:
        secure = scheme_hint
    else:
        secure = STORAGE_BACKEND == "s3"

    _client = Minio(
        host,
        access_key=os.getenv("S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID", ""),
        secret_key=os.getenv("S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        region=os.getenv("S3_REGION") or os.getenv("AWS_DEFAULT_REGION", ""),
        secure=secure,
    )
    return _client


def reset_storage_client():
    """Reset the cached client. Useful in tests."""
    global _client
    _client = None


def get_object_url(bucket_name: str, object_key: str) -> str:
    """Build a browser-reachable URL for the given bucket/key."""
    if STORAGE_BACKEND == "gcs":
        return f"https://storage.googleapis.com/{bucket_name}/{object_key}"

    if STORAGE_BACKEND == "minio":
        host, scheme_hint = _parse_endpoint(
            os.getenv("MINIO_URL", "http://localhost:9005")
        )
        scheme = "https" if scheme_hint else "http"
        return f"{scheme}://{host}/{bucket_name}/{object_key}"

    region = os.getenv("AWS_DEFAULT_REGION", "us-east-2")
    return f"https://{bucket_name}.s3.{region}.amazonaws.com/{object_key}"


def extract_object_key(file_url: str, bucket_name: str) -> str:
    """Extract the object key from a storage URL (S3, GCS, or MinIO)."""
    if "storage.googleapis.com" in file_url:
        # GCS: https://storage.googleapis.com/{bucket}/{key}
        return file_url.split(f"{bucket_name}/", 1)[1]
    if "amazonaws.com" in file_url:
        # S3: https://{bucket}.s3.{region}.amazonaws.com/{key}
        return file_url.split(".amazonaws.com/", 1)[1]
    # MinIO / custom: http://host:port/{bucket}/{key}
    return file_url.split(f"{bucket_name}/", 1)[1]


def ensure_bucket(client: Minio, bucket_name: str) -> None:
    """Create bucket with public policy if it doesn't exist. Policy only applies on S3/MinIO."""
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
