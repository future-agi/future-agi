"""Resolve the platform's existing outbound telephony configuration for ALK.

The hosted runner already uses LIVEKIT_OUTBOUND_TRUNK_ID and
PSTN_CALLER_NUMBER for phone agent definitions. ALK's call runner uses
different wire names; resolve those names here without asking the customer
for another trunk or caller ID.
"""

from __future__ import annotations

import os

from django.conf import settings


def platform_phone_telephony() -> dict[str, str]:
    def value(*names: str) -> str:
        for name in names:
            configured = str(
                getattr(settings, name, None) or os.environ.get(name) or ""
            ).strip()
            if configured:
                return configured
        return ""

    return {
        "LIVEKIT_URL": value("LIVEKIT_URL"),
        "LIVEKIT_API_KEY": value("LIVEKIT_API_KEY"),
        "LIVEKIT_API_SECRET": value("LIVEKIT_API_SECRET"),
        "SIP_OUTBOUND_TRUNK_ID": value(
            "LIVEKIT_OUTBOUND_TRUNK_ID", "SIP_OUTBOUND_TRUNK_ID"
        ),
        "SIP_OUTBOUND_FROM_NUMBER": value(
            "PSTN_CALLER_NUMBER", "SIP_OUTBOUND_FROM_NUMBER"
        ),
    }
