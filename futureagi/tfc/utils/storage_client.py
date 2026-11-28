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
import urllib3
from minio import Minio
from minio.error import S3Error

from tfc.utils.storage_config import (
    _parse_endpoint as _parse_endpoint,
)
from tfc.utils.storage_config import (
    _split_endpoint as _split_endpoint,
)
from tfc.utils.storage_config import (
    resolve_media_credentials,
    resolve_storage_config,
)
from tfc.utils.storage_config import (
    storage_http_client as _storage_http_client,
)

logger = structlog.get_logger(__name__)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "s3").lower()
# The MinIO that the compose files and the Helm chart (bundled mode) run: only
# there does the app keep agent source private in an existing bucket's policy.
STORAGE_BUNDLED = os.getenv("STORAGE_BUNDLED", "false").lower() == "true"
_client = None


def _configured_authority(raw_endpoint: str) -> tuple[str, bool | None]:
    """Validate like _parse_endpoint, but keep the host[:port] as configured.

    Stored object URLs are recognised as first-party by comparing their host
    with the raw MINIO_URL setting (tfc.utils.storage.is_own_storage_url), and
    Falcon builds its upload URLs from that setting (public_base_url). An IDN MINIO_URL
    must therefore stay in its configured (Unicode) form in generated URLs;
    HTTP clients and browsers apply IDNA themselves when they connect.
    """
    _, scheme_hint = _parse_endpoint(raw_endpoint)
    parsed, _ = _split_endpoint(raw_endpoint)
    return parsed.netloc, scheme_hint


def public_base_url(raw_endpoint: str) -> str:
    """MINIO_URL as a base to append /<bucket>/<key> to, validated first.

    Falcon has always built its upload URLs this way, keeping MINIO_URL's
    path prefix and configured host form. Surrounding whitespace, trailing
    slashes and any query or fragment are dropped so the result is one URL.
    """
    _parse_endpoint(raw_endpoint)
    parsed, has_scheme = _split_endpoint(raw_endpoint)
    base = parsed.netloc + parsed.path.rstrip("/")
    return f"{parsed.scheme}://{base}" if has_scheme else base


def storage_http_client() -> urllib3.PoolManager:
    # Preserve ordinary media's existing timeouts, retries and proxy/CA policy.
    return _storage_http_client(connect_timeout=300, read_timeout=300, retries=5)


def get_storage_client() -> Minio:
    """Return the ordinary-media client; never use it for private attachments."""
    global _client
    if _client is not None:
        return _client
    config = resolve_storage_config(backend=STORAGE_BACKEND)
    credentials = resolve_media_credentials(STORAGE_BACKEND)
    _client = Minio(
        config.endpoint,
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        region=config.region,
        secure=config.secure,
        http_client=storage_http_client(),
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
        host, scheme_hint = _configured_authority(
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
    try:
        browser_host, _ = _parse_endpoint(
            os.getenv("MINIO_URL", "http://localhost:9005")
        )
        internal_host, internal_secure = _parse_endpoint(
            os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
        )
    except ValueError:
        # Without both endpoints there is nothing to rewrite to; leave the URL
        # as stored rather than failing the caller.
        return file_url
    if browser_host == internal_host:
        return file_url
    parsed = urlparse(file_url)
    try:
        stored_host, _ = _parse_endpoint(parsed.netloc)
    except ValueError:
        return file_url
    if stored_host != browser_host:
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

# Everything the app reads only with its own keys is private; prefixes served by
# public URL (get_object_url) are the exceptions: run recordings and artifacts
# (alk-harness/, alk-sim/), Falcon attachments (falcon-ai/) and user uploads. A
# new prefix read only with the app's keys goes here.
PRIVATE_PREFIXES = (
    # Agent code: fix runs, uploaded sources, connected repository checkouts,
    # chat workspaces and the contracts read from them.
    "agent-fix/",
    "harness-sources/",
    "code-archives/",
    "harness-conversations/",
    "agent-contracts/",
    # Harness archives and records built around the agent.
    "harness-environments/",
    "harness-suites/",
    "harness-authoring/",
    "harness-sealed/",
    "harness-assembled/",
    "harness-bundles/",
    "harness-releases/",
    "harness-diagnostics/",
)
PRIVATE_OBJECTS_SID = "PrivateObjectsNotAnonymous"

# Buckets whose anonymous policy this process has already checked.
_checked_bucket_policies: set[str] = set()


def _anonymous_read_statement(bucket_name: str) -> dict:
    """Anyone may download an object by its URL, and do nothing else.

    Stored files reach browsers as plain object URLs (get_object_url), so
    anonymous reads are needed. Uploads, overwrites, deletes and listings all
    go through the backend's own credentials. _private_objects_statement takes
    agent source back out.
    """
    return {
        "Effect": "Allow",
        "Principal": {"AWS": ["*"]},
        "Action": [ANONYMOUS_READ_ACTION],
        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
    }


def _private_objects_statement(bucket_name: str) -> dict:
    """Nobody may download agent source by its URL, whatever an Allow says.

    Anonymous callers only: on S3 a Deny for "*" with no condition also refuses
    the backend's own signed and presigned reads. Keep the key lowercase: MinIO
    refuses "aws:PrincipalType" as a MalformedPolicy and accepts only
    "aws:principaltype"; AWS key names are case-insensitive. Values are lists,
    as MinIO stores them.
    """
    return {
        "Sid": PRIVATE_OBJECTS_SID,
        "Effect": "Deny",
        "Principal": {"AWS": ["*"]},
        "Action": [ANONYMOUS_READ_ACTION],
        "Resource": [
            f"arn:aws:s3:::{bucket_name}/{prefix}*" for prefix in PRIVATE_PREFIXES
        ],
        "Condition": {"StringEquals": {"aws:principaltype": ["Anonymous"]}},
    }


def _same_statement(stored: dict, wanted: dict) -> bool:
    # MinIO keeps resources as a set, so their order comes back changed.
    return {**stored, "Resource": sorted(stored.get("Resource") or [])} == {
        **wanted,
        "Resource": sorted(wanted["Resource"]),
    }


def _is_anonymous(principal) -> bool:
    if principal == "*":
        return True
    if isinstance(principal, dict):
        aws = principal.get("AWS")
        return aws == "*" or (isinstance(aws, list) and "*" in aws)
    return False


def _restrict_anonymous_access(
    policy: dict, bucket_name: str, *, private_objects: bool
) -> dict | None:
    """The policy with anonymous access cut down to object reads, or None if it
    already is. With private_objects, reads of agent source are refused too.
    Statements for named principals are kept."""
    statements = policy.get("Statement") or []
    if isinstance(statements, dict):
        statements = [statements]
    private = _private_objects_statement(bucket_name)
    ours = [s for s in statements if s.get("Sid") == PRIVATE_OBJECTS_SID]
    private_current = not private_objects or (
        len(ours) == 1 and _same_statement(ours[0], private)
    )
    kept, too_broad = [], False
    for statement in statements:
        if private_objects and statement.get("Sid") == PRIVATE_OBJECTS_SID:
            # Put back below with today's prefixes, so there is only ever one.
            continue
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
    if not too_broad and private_current:
        return None
    if too_broad and not any(
        statement.get("Effect") == "Allow" and _is_anonymous(statement.get("Principal"))
        for statement in kept
    ):
        kept.append(_anonymous_read_statement(bucket_name))
    if private_objects:
        kept.append(private)
    return {**policy, "Statement": kept}


def _tighten_existing_bucket_policy(client: Minio, bucket_name: str) -> bool:
    """Buckets created by earlier releases let anyone list, overwrite and delete
    every object; on the bundled MinIO, also read agent source by URL. Cut that
    back; never fail the caller. False when the policy could not be read or
    written for a reason that may pass, so the next call tries again."""
    try:
        current = client.get_bucket_policy(bucket_name)
    except Exception as exc:
        # The storage's own answer (NoSuchBucketPolicy, AccessDenied) stays the
        # same on the next call; a dropped connection may not.
        logger.debug(
            "storage_bucket_policy_unreadable", bucket=bucket_name, error=str(exc)
        )
        return isinstance(exc, S3Error)
    try:
        restricted = _restrict_anonymous_access(
            json.loads(current), bucket_name, private_objects=STORAGE_BUNDLED
        )
    except (TypeError, ValueError, AttributeError):
        return True
    if restricted is None:
        return True
    try:
        client.set_bucket_policy(bucket_name, json.dumps(restricted))
    except Exception as exc:
        logger.warning(
            "storage_bucket_policy_tighten_failed", bucket=bucket_name, error=str(exc)
        )
        return False
    logger.warning(
        "storage_bucket_policy_tightened",
        bucket=bucket_name,
        detail="anonymous access is limited to object reads",
    )
    return True


def ensure_bucket(client: Minio, bucket_name: str) -> None:
    """Create the bucket if needed, readable (not writable or listable) by URL,
    except agent source. Policy only applies on S3/MinIO."""
    if STORAGE_BACKEND == "gcs":
        # GCS buckets are pre-created via Terraform with IAM — skip
        return
    if not client.bucket_exists(bucket_name):
        try:
            client.make_bucket(bucket_name)
        except S3Error as exc:
            # Another worker created it between the check and here. Only this
            # code means ours: BucketAlreadyExists is someone else's bucket.
            if exc.code != "BucketAlreadyOwnedByYou":
                raise
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                _anonymous_read_statement(bucket_name),
                _private_objects_statement(bucket_name),
            ],
        }
        client.set_bucket_policy(bucket_name, json.dumps(policy))
        _checked_bucket_policies.add(bucket_name)
        return
    # MinIO only: an operator's own S3 bucket policy is theirs. Once per process,
    # counted only when the policy is known to be as tight as it gets.
    if (
        STORAGE_BACKEND == "minio"
        and bucket_name not in _checked_bucket_policies
        and _tighten_existing_bucket_policy(client, bucket_name)
    ):
        _checked_bucket_policies.add(bucket_name)
