"""Where a connection delivers, as the connection stores it (host_url).

A Langfuse or PostHog connection is stored with the address the user entered
(HOST_URL_PLATFORMS). Every other platform's destination follows from its
credentials, so the connection stores that destination, never an address the
user did not choose: the Datadog site's API host, Mixpanel's ingestion API, the
bucket (with its region and key prefix) or the queue the request logs are
written to, and the vendor API an alert or an issue is sent to.

settings_from_destination reads the stored destination back, for a connection
whose saved credentials cannot be read and whose keys are entered again.
"""

import re
from urllib.parse import quote, unquote, urlsplit

from integrations.services.datadog_service import DATADOG_SITES, DatadogService
from integrations.services.mixpanel_service import MixpanelService

PAGERDUTY_EVENTS_HOST = "https://events.pagerduty.com"
LINEAR_API_HOST = "https://api.linear.app"
# The host of an Amazon S3 destination as _storage_destination stores it.
_S3_HOST = re.compile(
    r"(?P<bucket>[A-Za-z0-9._-]+)\.s3\.(?P<region>[a-z0-9-]+)\.amazonaws\.com"
)


def _path(*parts: str) -> str:
    """The URL path of `parts`, each without leading or trailing slashes."""
    segments = [str(p).strip("/") for p in parts if p and str(p).strip("/")]
    return quote("/".join(segments), safe="/-_.~")


def _azure_account(connection_string: str) -> str:
    """The AccountName of an Azure Storage connection string, or ""."""
    for part in (connection_string or "").split(";"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "accountname":
            return value.strip()
    return ""


def _storage_destination(credentials: dict) -> str:
    sub_platform = credentials.get("sub_platform", "")
    prefix = credentials.get("prefix", "")
    if sub_platform == "s3":
        bucket = credentials.get("bucket", "").strip()
        region = credentials.get("region") or "us-east-1"
        return f"https://{bucket}.s3.{region}.amazonaws.com/{_path(prefix)}"
    if sub_platform == "azure_blob":
        account = _azure_account(credentials.get("connection_string", ""))
        container = credentials.get("container", "")
        return f"https://{account}.blob.core.windows.net/{_path(container, prefix)}"
    if sub_platform == "gcs":
        bucket = credentials.get("bucket", "")
        return f"https://storage.googleapis.com/{_path(bucket, prefix)}"
    return ""


def _queue_destination(credentials: dict) -> str:
    sub_platform = credentials.get("sub_platform", "")
    if sub_platform == "sqs":
        return (credentials.get("queue_url") or "").strip()
    if sub_platform == "pubsub":
        topic = credentials.get("topic_path", "")
        return f"https://pubsub.googleapis.com/v1/{_path(topic)}"
    return ""


def fixed_destination(platform: str, credentials: dict) -> str | None:
    """The destination of `platform` for these credentials.

    None for a platform whose address the user enters (HOST_URL_PLATFORMS);
    "" when the credentials do not name one (they then fail validation).
    """
    credentials = credentials or {}
    if platform == "datadog":
        return DatadogService().api_host(credentials)
    if platform == "mixpanel":
        return MixpanelService.API_HOST
    if platform == "cloud_storage":
        return _storage_destination(credentials)
    if platform == "message_queue":
        return _queue_destination(credentials)
    if platform == "pagerduty":
        return PAGERDUTY_EVENTS_HOST
    if platform == "linear":
        return LINEAR_API_HOST
    return None


def _s3_settings(host_url: str) -> dict | None:
    """The bucket, region and key prefix of a stored Amazon S3 destination."""
    try:
        parts = urlsplit(host_url)
    except ValueError:
        return None
    host = _S3_HOST.fullmatch(parts.netloc)
    if parts.scheme != "https" or host is None:
        return None
    settings = {
        "sub_platform": "s3",
        "bucket": host["bucket"],
        "region": host["region"],
        "prefix": unquote(parts.path).strip("/"),
    }
    # Only a destination stored for exactly these settings is read back.
    if _storage_destination(settings) != host_url:
        return None
    return settings


def settings_from_destination(platform: str, host_url: str) -> dict | None:
    """What a connection's credentials hold besides its keys, from its destination.

    {} when its keys are all they hold (Langfuse, PostHog, Mixpanel); the
    Datadog site; an S3 bucket with its region and key prefix. None when the
    rest cannot be known from the destination: Google Cloud Storage, Azure
    Blob and a message queue keep more than keys there, and a destination
    fixed_destination would not store is not guessed at.
    """
    host_url = host_url or ""
    if platform in ("langfuse", "posthog", "mixpanel"):
        return {}
    if platform == "datadog":
        for site in DATADOG_SITES:
            if fixed_destination(platform, {"site": site}) == host_url:
                return {"site": site}
        return None
    if platform == "cloud_storage":
        return _s3_settings(host_url)
    return None
