"""Tests for the baseline backfill — stamp config_hash + status on legacy
live EvalLogger rows so the reconciler has a real "as of migration" baseline.
Idempotent; never touches soft-deleted rows or rows whose config is gone."""

import uuid

import pytest

from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.trace import Trace
from tracer.services.eval_tasks.backfill import (
    backfill_blank_completed_status,
    backfill_config_hash_and_status,
)
from tracer.services.eval_tasks.config_hash import resolved_config_hash


def _entry(project, config, **kw):
    trace = Trace.objects.create(project=project, name=f"t-{uuid.uuid4().hex[:6]}")
    span = ObservationSpan.objects.create(
        id=f"s-{uuid.uuid4().hex[:8]}",
        project=project,
        trace=trace,
        name="s",
        observation_type="llm",
    )
    fields = {
        "target_type": EvalTargetType.SPAN,
        "observation_span": span,
        "trace": trace,
        "custom_eval_config": config,
        "config_hash": None,
    }
    fields.update(kw)
    return EvalLogger.objects.create(**fields)


@pytest.mark.integration
@pytest.mark.django_db
class TestBaselineBackfill:
    def test_stamps_hash_for_null_rows(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config)
        backfill_config_hash_and_status()
        entry.refresh_from_db()
        assert entry.config_hash == resolved_config_hash(custom_eval_config)

    def test_error_rows_become_errored(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config, error=True)
        backfill_config_hash_and_status()
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.ERRORED

    def test_skipped_rows_become_skipped(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config, skipped_reason="missing: input")
        backfill_config_hash_and_status()
        entry.refresh_from_db()
        assert entry.status == EvalEntryStatus.SKIPPED

    def test_preserves_existing_hash_and_is_idempotent(
        self, project, custom_eval_config
    ):
        entry = _entry(project, custom_eval_config, config_hash="a" * 64)
        backfill_config_hash_and_status()
        entry.refresh_from_db()
        assert entry.config_hash == "a" * 64  # not overwritten
        result = backfill_config_hash_and_status()
        assert result.hashed == 0  # re-run is a no-op

    def test_soft_deleted_left_untouched(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config)
        entry.delete()  # soft-delete
        backfill_config_hash_and_status()
        assert EvalLogger.all_objects.get(id=entry.id).config_hash is None

    def test_row_with_no_config_left_null(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config, custom_eval_config=None)
        backfill_config_hash_and_status()
        entry.refresh_from_db()
        assert entry.config_hash is None

    def test_multi_batch_sweep_visits_every_row(self, project, custom_eval_config):
        """A batch smaller than the row count exercises the keyset advancement
        (status pass, ``last_id = max(ids)``) and the per-config hash pass's
        inner ``while`` loop — the O(n) sweep the whole design rests on. Five
        rows over ``batch_size=2`` paginate 2 + 2 + 1; every row must be visited
        exactly once (no row skipped or re-counted)."""
        # The backfill sweeps EvalLogger globally; a committed row leaked from
        # another test would inflate the counts. Drain any pre-existing unstamped
        # rows first (idempotent) so the asserted run sees only this test's 5.
        backfill_config_hash_and_status()

        error_entries = [
            _entry(project, custom_eval_config, error=True) for _ in range(3)
        ]
        skipped_entries = [
            _entry(project, custom_eval_config, skipped_reason="missing: input")
            for _ in range(2)
        ]

        result = backfill_config_hash_and_status(batch_size=2)

        assert result.status_changed == 5
        assert result.hashed == 5
        for entry in error_entries:
            entry.refresh_from_db()
            assert entry.status == EvalEntryStatus.ERRORED
            assert entry.config_hash == resolved_config_hash(custom_eval_config)
        for entry in skipped_entries:
            entry.refresh_from_db()
            assert entry.status == EvalEntryStatus.SKIPPED
            assert entry.config_hash == resolved_config_hash(custom_eval_config)


def _blank_entry(project, config, **kw):
    """Create an entry then blank its status via queryset update — model
    validation forbids ``status=''`` on create, but prod rows written by the
    pre-0084 legacy writer carry exactly that, so tests must bypass it too."""
    entry = _entry(project, config, **kw)
    EvalLogger.all_objects.filter(id=entry.id).update(status="")
    entry.refresh_from_db()
    return entry


@pytest.mark.integration
@pytest.mark.django_db
class TestBlankCompletedBackfill:
    """Blank-status rows written by the legacy writer: stamp ``completed`` only
    when the row verifiably succeeded (no error/skip, real output present)."""

    def test_blank_row_with_output_becomes_completed(
        self, project, custom_eval_config
    ):
        entry = _blank_entry(project, custom_eval_config, output_float=0.9)
        changed = backfill_blank_completed_status()
        entry.refresh_from_db()
        assert changed == 1
        assert entry.status == EvalEntryStatus.COMPLETED

    def test_each_output_column_qualifies(self, project, custom_eval_config):
        entries = [
            _blank_entry(project, custom_eval_config, output_bool=True),
            _blank_entry(project, custom_eval_config, output_str="Passed"),
            _blank_entry(project, custom_eval_config, output_str_list=["a", "b"]),
        ]
        backfill_blank_completed_status()
        for entry in entries:
            entry.refresh_from_db()
            assert entry.status == EvalEntryStatus.COMPLETED

    def test_blank_row_without_output_left_untouched(
        self, project, custom_eval_config
    ):
        entry = _blank_entry(project, custom_eval_config)
        changed = backfill_blank_completed_status()
        entry.refresh_from_db()
        assert changed == 0
        assert entry.status == ""

    def test_error_row_not_stamped_completed(self, project, custom_eval_config):
        """Legacy error rows carry error=True + output_str='ERROR' — they belong
        to the errored pass, never to this one."""
        entry = _blank_entry(
            project, custom_eval_config, error=True, output_str="ERROR"
        )
        changed = backfill_blank_completed_status()
        entry.refresh_from_db()
        assert changed == 0
        assert entry.status == ""

    def test_error_sentinel_without_flag_not_stamped(
        self, project, custom_eval_config
    ):
        entry = _blank_entry(project, custom_eval_config, output_str="ERROR")
        backfill_blank_completed_status()
        entry.refresh_from_db()
        assert entry.status == ""

    def test_soft_deleted_left_untouched(self, project, custom_eval_config):
        entry = _entry(project, custom_eval_config, output_float=0.5)
        entry.delete()  # soft-delete first: delete() saves, which re-validates
        EvalLogger.all_objects.filter(id=entry.id).update(status="")
        backfill_blank_completed_status()
        assert EvalLogger.all_objects.get(id=entry.id).status == ""

    def test_idempotent(self, project, custom_eval_config):
        _blank_entry(project, custom_eval_config, output_float=0.5)
        assert backfill_blank_completed_status() == 1
        assert backfill_blank_completed_status() == 0

    def test_dry_run_counts_without_writing(self, project, custom_eval_config):
        entry = _blank_entry(project, custom_eval_config, output_float=0.5)
        assert backfill_blank_completed_status(dry_run=True) == 1
        entry.refresh_from_db()
        assert entry.status == ""

    def test_multi_batch_sweep_visits_every_row(self, project, custom_eval_config):
        """batch_size smaller than the row count exercises the keyset
        advancement — every row visited exactly once."""
        entries = [
            _blank_entry(project, custom_eval_config, output_bool=True)
            for _ in range(5)
        ]
        changed = backfill_blank_completed_status(batch_size=2)
        assert changed == 5
        for entry in entries:
            entry.refresh_from_db()
            assert entry.status == EvalEntryStatus.COMPLETED
