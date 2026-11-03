"""LiveKit configuration — reads from environment variables.

Trunk naming follows OUR call direction, not SIP protocol direction:
  - ``sip_inbound_trunk_id``  → for **inbound** calls (we dial user's agent)
    LiveKit SDK: SIPOutboundTrunkInfo  (LiveKit dials OUT via the SIP trunk)
  - ``sip_outbound_trunk_id`` → for **outbound** calls (user's agent calls us)
    LiveKit SDK: SIPInboundTrunkInfo   (SIP trunk routes IN to LiveKit)

Resolution chain for each trunk ID:
  1. Environment variable (production)
  2. Django Redis cache (local dev — populated by ``setup_sip_trunk`` command)
  3. Empty string (trunk not provisioned)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

# Cache keys written by ``python manage.py setup_sip_trunk`` at startup.
CACHE_KEY_INBOUND_TRUNK = "livekit:sip_inbound_trunk_id"
CACHE_KEY_OUTBOUND_TRUNK = "livekit:sip_outbound_trunk_id"


def _resolve_config(env_var: str, cache_key: str) -> str:
    """Resolve a config value: env var → Redis cache → empty.

    Env var always takes priority (production override). When set,
    it back-fills the cache so agent workers without the env var
    can read it.  Cache is the primary store in local dev (populated
    by ``setup_sip_trunk``).
    """
    # 1. Env var always wins — prevents stale cache from overriding
    val = os.environ.get(env_var, "").strip()
    if val:
        try:
            from django.core.cache import cache

            cache.set(cache_key, val, timeout=None)
        except Exception:
            pass
        return val

    # 2. Redis cache (local dev — populated by management command)
    try:
        from django.core.cache import cache

        cached = cache.get(cache_key, "")
        if cached:
            return cached
    except Exception:
        pass

    return ""


@dataclass(frozen=True)
class LiveKitConfig:
    """Immutable LiveKit connection and resource configuration."""

    url: str
    api_key: str
    api_secret: str
    sip_inbound_trunk_id: str  # For inbound calls (SIP outbound trunk)
    sip_outbound_trunk_id: str  # For outbound calls (SIP inbound trunk)
    egress_bucket: str
    # AWS S3 credentials for egress recordings
    s3_access_key: str
    s3_secret_key: str
    s3_region: str
    # Backend callback URL — included in agent dispatch metadata so the
    # agent worker knows where to send transcripts/signals back to.
    backend_callback_url: str

    @classmethod
    def from_env(cls) -> LiveKitConfig:
        config = cls(
            url=os.environ.get("LIVEKIT_URL", ""),
            api_key=os.environ.get("LIVEKIT_API_KEY", ""),
            api_secret=os.environ.get("LIVEKIT_API_SECRET", ""),
            sip_inbound_trunk_id=_resolve_config(
                "SIP_INBOUND_TRUNK_ID", CACHE_KEY_INBOUND_TRUNK
            ),
            sip_outbound_trunk_id=_resolve_config(
                "SIP_OUTBOUND_TRUNK_ID", CACHE_KEY_OUTBOUND_TRUNK
            ),
            egress_bucket=os.environ.get("AWS_BUCKET_NAME", ""),
            s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID", ""),
            s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
            s3_region=os.environ.get("AWS_DEFAULT_REGION", ""),
            backend_callback_url=os.environ.get(
                "LIVEKIT_BACKEND_CALLBACK_URL", "http://localhost:8000"
            ),
        )
        if not config.api_key or not config.api_secret:
            logger.warning(
                "livekit_missing_credentials",
                hint="Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET env vars",
            )
        if not config.sip_inbound_trunk_id:
            logger.warning(
                "livekit_no_sip_inbound_trunk",
                hint="SIP_INBOUND_TRUNK_ID not set — inbound calls will fail at SIP dial-out",
            )
        return config

    @property
    def http_url(self) -> str:
        """Convert ws(s):// URL to http(s):// for REST API calls."""
        return self.url.replace("wss://", "https://").replace("ws://", "http://")
