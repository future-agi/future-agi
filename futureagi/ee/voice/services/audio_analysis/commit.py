"""Persist only the envelope, guarded by the row's current generation/identity."""

from django.db import transaction

from .errors import StaleAnalysisError


def lock_audio_analysis_call(call_id: str):
    """Lock RunTest -> TestExecution -> CallExecution in the caller's transaction.

    A joined FOR UPDATE does not guarantee acquisition order. Discover the parent
    ids without locks, then recheck the relationships and tombstones under all
    three locks so a concurrent reparent/delete cannot authorize a stale write.
    NO KEY UPDATE still excludes soft deletion but permits child FK checks.
    """
    from simulate.models.run_test import RunTest
    from simulate.models.test_execution import CallExecution, TestExecution

    try:
        execution_id, run_id = (
            CallExecution.objects.filter(id=call_id)
            .values_list("test_execution_id", "test_execution__run_test_id")
            .get()
        )
        run = RunTest.objects.select_for_update(of=("self",), no_key=True).get(
            id=run_id
        )
        execution = TestExecution.objects.select_for_update(
            of=("self",), no_key=True
        ).get(id=execution_id)
        call = CallExecution.objects.select_for_update(of=("self",), no_key=True).get(
            id=call_id
        )
    except (
        RunTest.DoesNotExist,
        TestExecution.DoesNotExist,
        CallExecution.DoesNotExist,
    ):
        raise StaleAnalysisError("tombstone") from None
    if run.deleted or execution.deleted or call.deleted:
        raise StaleAnalysisError("tombstone")
    if execution.run_test_id != run.id or call.test_execution_id != execution.id:
        raise StaleAnalysisError("stale_identity")
    execution.run_test = run
    call.test_execution = execution
    return call


def commit_envelope(
    call_id: str, generation: int, analysis_id: str, envelope: dict
) -> None:
    with transaction.atomic():
        call = lock_audio_analysis_call(call_id)
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
