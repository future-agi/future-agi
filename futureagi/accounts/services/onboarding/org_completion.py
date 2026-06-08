import structlog

logger = structlog.get_logger(__name__)


def _workspace_has_real_product_setup(*, user, organization, workspace):
    if not organization or not workspace:
        return False

    try:
        from accounts.services.onboarding.signal_resolver import (
            collect_onboarding_signals,
            signals_have_real_product_setup,
        )

        signals = collect_onboarding_signals(
            user=user,
            organization=organization,
            workspace=workspace,
        )
        return signals_have_real_product_setup(signals)
    except Exception as exc:
        logger.warning(
            "Product setup signal check failed",
            error=str(exc),
            organization_id=str(getattr(organization, "id", "")),
            workspace_id=str(getattr(workspace, "id", "")),
            user_id=str(getattr(user, "id", "")),
        )
        return False


def organization_has_completed_product_setup(organization, *, user, workspace=None):
    if not organization:
        return False

    from accounts.models import OnboardingActivationEvent

    if OnboardingActivationEvent.no_workspace_objects.filter(
        organization=organization,
        event_name="first_quality_loop_completed",
        is_sample=False,
    ).exists():
        return True

    return _workspace_has_real_product_setup(
        user=user,
        organization=organization,
        workspace=workspace,
    )
