"""Maintenance activities for bounded SAML login state."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from saml2_auth.models import SamlLoginAttempt, SamlResponseCandidate
from tfc.temporal.drop_in import temporal_activity


@temporal_activity(time_limit=300, queue="default")
def purge_login_state():
    """Reclaim expired response material and retained terminal attempts."""

    now = timezone.now()
    attempt_cutoff = now - timedelta(seconds=settings.SAML_ATTEMPT_RETENTION_SECONDS)
    deleted_candidates = _purge_in_batches(SamlResponseCandidate, now)
    deleted_attempts = _purge_in_batches(SamlLoginAttempt, attempt_cutoff)
    return {"candidates": deleted_candidates, "attempts": deleted_attempts}


def _purge_in_batches(model, before):
    deleted = 0
    for _ in range(50):
        ids = list(
            model.objects.filter(expires_at__lt=before)
            .order_by("expires_at")
            .values_list("id", flat=True)[:1000]
        )
        if not ids:
            break
        count, _ = model.objects.filter(id__in=ids).delete()
        deleted += count
    return deleted
