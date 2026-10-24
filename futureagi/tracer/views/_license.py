"""
License gates for the Enterprise-only Observe features: Instant eval and
Insights.

They are gated the way the error feed is
(``tracer.views.feed._permissions.ErrorFeedLicenseRequired``): the check runs
in ``APIView.initial``, after authentication and before any handler, so it
covers every method of a view. A denial is the shared HTTP 402
FeatureUnavailable response, so the frontend handles it as it does the error
feed's.
"""

from tfc.ee_gating import EEFeature


class _LicenseRequired:
    ee_feature: EEFeature

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        from tfc.ee_gating import check_ee_feature

        org = getattr(request, "organization", None) or getattr(
            request.user, "organization", None
        )
        check_ee_feature(self.ee_feature, org_id=str(org.id) if org else None)


class InstantEvalLicenseRequired(_LicenseRequired):
    """Mixin denying Instant eval APIs without the instant_eval entitlement:
    a self-hosted license that lists it, or a Cloud plan with it."""

    ee_feature = EEFeature.INSTANT_EVAL


class InsightsLicenseRequired(_LicenseRequired):
    """Mixin denying Insights APIs without the insights entitlement: a
    self-hosted license that lists it, or a Cloud plan with it."""

    ee_feature = EEFeature.INSIGHTS
