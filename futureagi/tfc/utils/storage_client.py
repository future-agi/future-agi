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
            region=os.getenv("MINIO_REGION") or "auto",
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


def server_reachable_url(file_url: str) -> str:
    """The same object, addressed the way a process inside the deployment can reach it.

    A stored MinIO URL names the browser host, which is not resolvable server side; S3 and GCS
    URLs already are, so they come back unchanged.
    """
    if STORAGE_BACKEND != "minio":
        return file_url
    browser_host, _ = _parse_endpoint(os.getenv("MINIO_URL", "http://localhost:9005"))
    internal_host, internal_secure = _parse_endpoint(
        os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
    )
    if not browser_host or not internal_host or browser_host == internal_host:
        return file_url
    parsed = urlparse(file_url)
    if parsed.netloc != browser_host:
        return file_url
    scheme = "https" if internal_secure else "http"
    return parsed._replace(scheme=scheme, netloc=internal_host).geturl()


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


ANONYMOUS_READ_ACTION = "s3:GetObject"

# Buckets whose anonymous policy this process has already checked.
_checked_bucket_policies: set[str] = set()


def _anonymous_read_statement(bucket_name: str) -> dict:
    """Anyone may download an object by its URL, and do nothing else.

    Stored files reach browsers as plain object URLs (get_object_url), so
    anonymous reads are needed. Uploads, overwrites, deletes and listings all
    go through the backend's own credentials.
    """
    return {
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": [ANONYMOUS_READ_ACTION],
        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
    }


def _is_anonymous(principal) -> bool:
    if principal == "*":
        return True
    if isinstance(principal, dict):
        aws = principal.get("AWS")
        return aws == "*" or (isinstance(aws, list) and "*" in aws)
    return False


def _restrict_anonymous_access(policy: dict, bucket_name: str) -> dict | None:
    """The policy with anonymous access cut down to object reads, or None if it
    already grants nothing more. Statements for named principals are kept."""
    statements = policy.get("Statement") or []
    if isinstance(statements, dict):
        statements = [statements]
    kept, too_broad = [], False
    for statement in statements:
        if statement.get("Effect") == "Allow" and _is_anonymous(
            statement.get("Principal")
        ):
            actions = statement.get("Action") or []
            if isinstance(actions, str):
                actions = [actions]
            if any(action != ANONYMOUS_READ_ACTION for action in actions):
                too_broad = True
                continue
        kept.append(statement)
    if not too_broad:
        return None
    if not any(
        statement.get("Effect") == "Allow" and _is_anonymous(statement.get("Principal"))
        for statement in kept
    ):
        kept.append(_anonymous_read_statement(bucket_name))
    return {**policy, "Statement": kept}


def _tighten_existing_bucket_policy(client: Minio, bucket_name: str) -> None:
    """Buckets created by earlier releases let anyone list, overwrite and delete
    every object. Cut that back to reads once per process; never fail the caller."""
    try:
        current = client.get_bucket_policy(bucket_name)
    except Exception as exc:  # NoSuchBucketPolicy, or a policy we may not read
        logger.debug(
            "storage_bucket_policy_unreadable", bucket=bucket_name, error=str(exc)
        )
        return
    try:
        restricted = _restrict_anonymous_access(json.loads(current), bucket_name)
    except (TypeError, ValueError, AttributeError):
        return
    if restricted is None:
        return
    try:
        client.set_bucket_policy(bucket_name, json.dumps(restricted))
    except Exception as exc:
        logger.warning(
            "storage_bucket_policy_tighten_failed", bucket=bucket_name, error=str(exc)
        )
        return
    logger.warning(
        "storage_bucket_policy_tightened",
        bucket=bucket_name,
        detail="anonymous access is now limited to reading objects by URL",
    )


def ensure_bucket(client: Minio, bucket_name: str) -> None:
    """Create the bucket if needed, readable (not writable or listable) by URL.
    Policy only applies on S3/MinIO."""
    if STORAGE_BACKEND == "gcs":
        # GCS buckets are pre-created via Terraform with IAM — skip
        return
    if not client.bucket_exists(bucket_name):
        client.make_bucket(bucket_name)
        policy = {
            "Version": "2012-10-17",
            "Statement": [_anonymous_read_statement(bucket_name)],
        }
        client.set_bucket_policy(bucket_name, json.dumps(policy))
        _checked_bucket_policies.add(bucket_name)
        return
    # Only the bundled MinIO: an operator's own S3 bucket policy is theirs.
    if STORAGE_BACKEND == "minio" and bucket_name not in _checked_bucket_policies:
        _checked_bucket_policies.add(bucket_name)
        _tighten_existing_bucket_policy(client, bucket_name)
