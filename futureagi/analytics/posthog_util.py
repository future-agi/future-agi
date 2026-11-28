import os

import structlog
from django.core.cache import cache
from posthog import Posthog

logger = structlog.get_logger(__name__)

IDENTITY_CACHE_TIMEOUT = 400 * 24 * 60 * 60
_token_warning_logged = False


class PostHogTracker:
    """Server-side PostHog events. Off, with no network call, unless
    POSTHOG_API_KEY is set — self-hosted installs leave it empty."""

    def __init__(self):
        self.api_key = (os.getenv("POSTHOG_API_KEY") or "").strip() or None
        self.host = os.getenv("POSTHOG_HOST") or "https://us.i.posthog.com"
        self.client = None
        self._init_client()

    def _init_client(self):
        global _token_warning_logged
        if not self.api_key:
            if not _token_warning_logged:
                # Running without PostHog is the normal self-hosted setup.
                logger.debug("posthog_disabled", reason="POSTHOG_API_KEY not set")
                _token_warning_logged = True
            return
        self.client = Posthog(self.api_key, host=self.host)

    @property
    def is_enabled(self) -> bool:
        return self.client is not None

    # ---- User Identification (v7: use set/set_once instead of identify) ----

    def identify_user(self, user, org=None, workspace_id=None) -> bool:
        """Link the legacy email and report whether the profile update was queued."""
        if self.client is None:
            return False
        try:
            distinct_id = str(user.id)
            properties = {
                "email": user.email,
                "name": getattr(user, "name", ""),
            }
            if org:
                properties["organization_id"] = str(org.id)
                properties["organization_name"] = (
                    getattr(org, "display_name", None) or org.name
                )
            if workspace_id:
                properties["workspace_id"] = str(workspace_id)

            try:
                alias_key = f"posthog:email-alias:{distinct_id}"
                if user.email and cache.add(alias_key, True, IDENTITY_CACHE_TIMEOUT):
                    try:
                        self.client.alias(
                            previous_id=user.email, distinct_id=distinct_id
                        )
                    except Exception:
                        cache.delete(alias_key)
                        raise
            finally:
                # A cache or alias failure must not suppress the profile update.
                self.client.set(distinct_id=distinct_id, properties=properties)
            return True
        except Exception:
            logger.warning("posthog_identify_failed", exc_info=True)
            return False

    # ---- Group Analytics ----

    def set_organization(self, org, subscription=None):
        """Set organization group properties."""
        if not self.is_enabled or not org:
            return
        try:
            properties = {
                "name": getattr(org, "display_name", None) or org.name,
            }
            if subscription:
                properties["subscription_tier"] = subscription
            self.client.group_identify("organization", str(org.id), properties)
        except Exception as e:
            logger.exception("PostHog org group error", error=str(e))

    def set_workspace(self, workspace_id, org_id=None, properties=None):
        """Set workspace group properties."""
        if not self.is_enabled or not workspace_id:
            return
        try:
            props = properties or {}
            if org_id:
                props["organization_id"] = str(org_id)
            self.client.group_identify("workspace", str(workspace_id), props)
        except Exception as e:
            logger.exception("PostHog workspace group error", error=str(e))

    # ---- Event Tracking (v7: event name first, distinct_id as kwarg) ----

    def capture(self, user_id, event_name, properties=None, groups=None):
        """Capture an event. groups = {"organization": org_id, "workspace": ws_id}"""
        if not self.is_enabled:
            return
        try:
            kwargs = {"properties": properties or {}, "groups": groups or {}}
            if user_id is not None:
                kwargs["distinct_id"] = str(user_id)
            self.client.capture(event_name, **kwargs)
        except Exception as e:
            logger.exception("PostHog capture error", error=str(e))

    # ---- Feature Flags ----

    def is_feature_enabled(self, flag_name, user_id, groups=None):
        """Check if a feature flag is enabled for a user."""
        if not self.is_enabled:
            return False
        try:
            return self.client.feature_enabled(
                flag_name,
                str(user_id),
                groups=groups or {},
            )
        except Exception as e:
            logger.exception("PostHog feature flag error", error=str(e))
            return False

    def get_feature_flag_payload(self, flag_name, user_id, groups=None):
        """Get feature flag payload for multivariate flags."""
        if not self.is_enabled:
            return None
        try:
            return self.client.get_feature_flag_payload(
                flag_name,
                str(user_id),
                groups=groups or {},
            )
        except Exception as e:
            logger.exception("PostHog feature flag payload error", error=str(e))
            return None

    def shutdown(self):
        """Flush and close the client."""
        if self.client:
            self.client.shutdown()


posthog_tracker = PostHogTracker()
