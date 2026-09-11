"""Regression tests for poison-pill isolation in span batch ingestion.

A batch of spans is persisted with a single PostgreSQL ``COPY`` (all-or-nothing).
``_serialize_json_field_value`` already scrubs the two known JSON triggers
(non-finite floats and ``\\x00`` NUL bytes), but the COPY fast path only has a
per-row fallback for primary-key collisions. Any *other* unwritable row — an
over-length ``CharField`` value, a numeric overflow, a future field/constraint —
would still fail the COPY and silently drop *every* span in the batch (the
ingest API has already returned 200, the Temporal activity runs with
``max_retries=0``).

These tests use an over-length ``name`` (the column is ``varchar(2000)``, which
scrubbing does not touch) to pin the contract: healthy spans persist, only the
bad row(s) are dropped, and a batch where *nothing* writes still raises
(systemic failure / all-bad batch must stay loud).
"""

import uuid
from datetime import timedelta

import psycopg
import pytest
from django.db import DatabaseError
from django.utils import timezone

from tracer.models.observation_span import ObservationSpan
from tracer.utils.trace_ingestion import _bulk_insert_observation_spans

# `name` is varchar(2000); this overflows it and is rejected by COPY with a
# non-PK DataError (SQLSTATE 22001) that serialization scrubbing does not catch.
_OVERLONG_NAME = "x" * 3000


def _make_span(project, trace, *, name="span", span_input=None):
    """Build an unsaved ObservationSpan mirroring the conftest fixture."""
    return ObservationSpan(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=name,
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input=span_input if span_input is not None else {"content": "hello"},
        status="OK",
    )


@pytest.mark.integration
class TestSpanBatchPoisonIsolation:
    def test_poison_row_does_not_drop_healthy_spans(self, db, project, trace):
        """An unwritable row must not take down the rest of the batch."""
        good_a = _make_span(project, trace, name="good_a")
        poison = _make_span(project, trace, name=_OVERLONG_NAME)
        good_b = _make_span(project, trace, name="good_b")

        # Must not raise: the bad row is isolated, the rest is persisted.
        _bulk_insert_observation_spans([good_a, poison, good_b])

        persisted = set(
            ObservationSpan.objects.filter(
                id__in=[good_a.id, poison.id, good_b.id]
            ).values_list("id", flat=True)
        )
        assert good_a.id in persisted
        assert good_b.id in persisted
        assert poison.id not in persisted

    def test_all_rows_failing_stays_loud(self, db, project, trace):
        """If nothing can be written, the DB error must propagate (stay loud).

        Asserts the *specific* database error, not a bare Exception: a broad
        ``pytest.raises(Exception)`` would stay green even if the isolation
        fallback were removed and the raw batch error simply propagated.
        """
        poison_a = _make_span(project, trace, name=_OVERLONG_NAME)
        poison_b = _make_span(project, trace, name=_OVERLONG_NAME)

        with pytest.raises((DatabaseError, psycopg.Error)):
            _bulk_insert_observation_spans([poison_a, poison_b])

    def test_clean_batch_uses_fast_path(self, db, project, trace):
        """The happy path is unchanged: a clean batch persists in full."""
        spans = [_make_span(project, trace, name=f"clean_{i}") for i in range(3)]

        _bulk_insert_observation_spans(spans)

        assert (
            ObservationSpan.objects.filter(id__in=[s.id for s in spans]).count() == 3
        )

    def test_large_batch_isolates_single_poison(self, db, project, trace):
        """One bad row buried in a large batch drops only itself (bisection)."""
        good = [_make_span(project, trace, name=f"good_{i}") for i in range(20)]
        poison = _make_span(project, trace, name=_OVERLONG_NAME)
        batch = good[:10] + [poison] + good[10:]

        _bulk_insert_observation_spans(batch)

        assert (
            ObservationSpan.objects.filter(id__in=[s.id for s in good]).count() == 20
        )
        assert not ObservationSpan.objects.filter(id=poison.id).exists()
