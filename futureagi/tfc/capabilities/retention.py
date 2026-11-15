"""Current source-retention policy through the maintained EE boundary.

The Cloud trace-retention worker and EE plan entitlements resolve this same
organization-scoped policy. Do not cache it on a metric or manifest: every read
must observe current policy. OSS without the policy adapter fails closed.
"""

from django.core.exceptions import ImproperlyConfigured

from tfc.capabilities.service import get_deployment_flavor
from tfc.ee_loader import has_ee
from tfc.licensing.types import DeploymentFlavor


def current_trace_retention_days(organization_id):
    if get_deployment_flavor() == DeploymentFlavor.OSS or not has_ee(
        "ee.usage.services.entitlements"
    ):
        raise ImproperlyConfigured(
            "Metrics requires the maintained EE trace-retention policy; "
            "an OSS source-retention policy is not configured."
        )
    from ee.usage.services.entitlements import Entitlements

    # Privacy decisions cannot depend on cache invalidation or its TTL. Inherited
    # plan changes and failed Redis invalidations must take effect on this read.
    days = Entitlements.get_retention_days(
        str(organization_id), "traces", use_cache=False
    )
    # The maintained provider uses -1 for explicit unlimited retention. Missing,
    # malformed, or other negative policy values must never turn into unlimited.
    if type(days) is not int or (days != -1 and days <= 0):
        raise ImproperlyConfigured("Invalid current trace-retention policy.")
    return days
