import os

import structlog
from mixpanel import Consumer, Mixpanel

from accounts.models import User
from tfc.middleware.workspace_context import get_current_organization

logger = structlog.get_logger(__name__)
try:
    from ee.usage.models.usage import OrganizationSubscription
except ImportError:
    OrganizationSubscription = None

# Track if we've already noted the missing token
_token_warning_logged = False

# Mixpanel's client waits forever by default, and it runs inline on signup and
# login; bound each request so a slow Mixpanel can't hold an auth request open.
# No retries either: its default of 4 turns one unreachable call into ~25s, and
# login makes three of them.
MIXPANEL_REQUEST_TIMEOUT_SECONDS = 5
MIXPANEL_RETRY_LIMIT = 0


class MixpanelTracker:
    """Server-side Mixpanel events. Off, with no network call, unless
    MIX_PANEL_TOKEN is set — self-hosted installs leave it empty."""

    def __init__(self):
        self.token = (os.getenv("MIX_PANEL_TOKEN") or "").strip() or None
        self.mp = (
            Mixpanel(
                self.token,
                consumer=Consumer(
                    request_timeout=MIXPANEL_REQUEST_TIMEOUT_SECONDS,
                    retry_limit=MIXPANEL_RETRY_LIMIT,
                ),
            )
            if self.token
            else None
        )
        self._check_token()

    def _check_token(self) -> bool:
        """Check if Mixpanel token is configured. Notes it once, at debug:
        running without Mixpanel is the normal self-hosted setup."""
        global _token_warning_logged
        if not self.token:
            if not _token_warning_logged:
                logger.debug("mixpanel_disabled", reason="MIX_PANEL_TOKEN not set")
                _token_warning_logged = True
            return False
        return True

    def _is_enabled(self) -> bool:
        """Check if Mixpanel is enabled (token is configured)."""
        return self.mp is not None

    def update_org_details(self, org_id, org_name, subscription):
        """Update organization details in Mixpanel"""
        if not self._is_enabled():
            return
        try:
            self.mp.group_set(
                "org_id",
                str(org_id),
                {
                    "Organization Name": org_name,
                    "Subscription Tier": subscription,
                },
            )
        except Exception as e:
            logger.exception(f"Error updating organization details in Mixpanel: {e}")

    def set_details(self, user: User):
        """Set the user's Mixpanel profile and org group. Never raises: this
        runs inline on signup, which must not fail because Mixpanel did."""
        if not self._is_enabled():
            return
        try:
            self._set_details(user)
        except Exception:
            logger.exception("mixpanel_set_details_failed", user_id=str(user.id))

    def _set_details(self, user: User):
        from accounts.utils import get_user_organization

        _org = get_current_organization() or get_user_organization(user)
        if not _org:
            logger.warning(
                "set_details: no organization found for user", user_id=str(user.id)
            )
            return
        if OrganizationSubscription is None:
            # No subscription model when ee is absent.
            subscription = "self-hosted"
        else:
            try:
                subscription = OrganizationSubscription.objects.get(
                    organization=_org
                ).get_subscription_name()
            except OrganizationSubscription.DoesNotExist:
                subscription = "Unknown"
        self.mp.group_set_once(
            "org_id",
            str(_org.id),
            {
                "Organization Name": _org.display_name or _org.name,
                "Subscription Tier": subscription,
            },
        )
        self.mp.people_set_once(
            str(user.id),
            {
                "id": str(user.id),
                "$email": user.email,
                "$name": user.name,
                "org_id": [str(_org.id)],
                "org_name": _org.display_name or _org.name,
            },
        )

    def track_event(self, event_name, properties=None):
        """Send event data to Mixpanel"""
        if not self._is_enabled():
            return
        try:
            properties = properties or {}
            if "org_id" in properties and "org_name" in properties:
                subscription = properties.pop("subscription", None)
                self.mp.group_set_once(
                    "org_id",
                    str(properties.get("org_id")[0]),
                    {
                        "Organization Name": properties.get("org_name"),
                        "Subscription Tier": subscription,
                    },
                )
                self.mp.people_set_once(
                    str(properties.get("$user_id")),
                    {"org_id": properties.get("org_id")},
                )
            user_id = str(properties.get("$user_id", "unknown"))
            self.mp.track(user_id, event_name, properties)
        except Exception as e:
            logger.exception(f"Error tracking Mixpanel event: {e}")


mixpanel_tracker = MixpanelTracker()
