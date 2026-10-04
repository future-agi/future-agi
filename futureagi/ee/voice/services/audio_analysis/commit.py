"""Persist only the envelope, guarded by the row's current generation/identity."""

from django.db import transaction

from .errors import StaleAnalysisError


def commit_envelope(
    call_id: str, generation: int, analysis_id: str, envelope: dict
) -> None:
    from simulate.models.test_execution import CallExecution

    with transaction.atomic():
        try:
            call = CallExecution.objects.select_for_update(of=("self",)).get(
                id=call_id, deleted=False
            )
        except CallExecution.DoesNotExist:
            raise StaleAnalysisError("tombstone") from None
        if call.audio_analysis_generation != generation:
            raise StaleAnalysisError("stale_generation")
        stored = call.audio_metrics or {}
        if stored.get("analysis_id") != analysis_id:
            raise StaleAnalysisError("stale_identity")
        if (
            envelope.get("analysis_id") != analysis_id
            or envelope.get("generation") != generation
        ):
            raise StaleAnalysisError("stale_identity")
        # A retry after the first terminal commit must not overwrite its result.
        if stored.get("computed_at") and stored.get("state") != "pending":
            return
        call.audio_metrics = envelope
        call.save(update_fields=["audio_metrics"])
