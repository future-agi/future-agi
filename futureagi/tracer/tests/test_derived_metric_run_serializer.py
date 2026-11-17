"""Read-only run provenance without a database or a current-version lookup."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.serializers.derived_metric import (
    DerivedMetricRunSerializer,
    DerivedMetricVersionSerializer,
)


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
@pytest.mark.parametrize("enrolled", [False, True])
def test_run_serializes_its_immutable_definition_without_queries(status, enrolled):
    from tracer.services.derived_metrics.snapshots import SUBMISSION_PROTOCOL

    metric = DerivedMetric(id=UUID(int=1), current_version=2, name="Current metric")
    version = DerivedMetricVersion(
        metric=metric,
        version=1,
        formula="ratio(resolved == 1, resolved >= 0)",
        inputs=[
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
        selection_unit="session",
        weighting="none",
        cohort_key="",
        filters=[],
        definition_hash="a" * 64,
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
    )
    run = DerivedMetricRun(
        definition=version,
        window_start=datetime(2026, 10, 25, tzinfo=UTC),
        window_end=datetime(2026, 10, 26, tzinfo=UTC),
        as_of=datetime(2026, 10, 26, tzinfo=UTC),
        filters=[],
        status=status,
        source_manifest={"write_protocol": SUBMISSION_PROTOCOL} if enrolled else None,
    )
    serialized = DerivedMetricRunSerializer(run).data
    assert serialized["source_manifest"] is None
    assert serialized["status"] == status
    assert serialized["metric_version"] == 1
    assert serialized["definition"] == DerivedMetricVersionSerializer(version).data
    assert serialized["definition"]["selection_unit"] == "session"
    assert serialized["definition"]["weighting"] == "none"
    assert DerivedMetricRunSerializer().fields["definition"].read_only is True
    incoming = DerivedMetricRunSerializer(
        data={"definition": {"formula": "sum(resolved)"}}
    )
    parsed = incoming.to_internal_value(
        {
            "definition": {"formula": "sum(resolved)"},
            "metric_id": str(metric.id),
            "metric_version": 1,
            "window_start": run.window_start,
            "window_end": run.window_end,
            "as_of": run.as_of,
            "filters": [],
        }
    )
    # Existing source-mapped identity fields nest under definition, but the new
    # read-only recipe must never accept a formula/inputs mutation.
    assert parsed["definition"] == {"metric_id": metric.id, "version": 1}
    assert serialized["definition"]["needs_rebuild"] is False
    assert serialized["definition"]["kind"] == "custom"
    assert serialized["definition"]["level"] == "conversation"
    assert serialized["kind"] == "run"
    assert serialized["pinned_generation"] is None
    assert serialized["changed_at"] is None


def test_legacy_user_version_reports_needs_rebuild_without_a_database():
    version = DerivedMetricVersion(
        selection_unit="user",
        level="conversation",
        kind="custom",
        user_rule="",
        formula="sum(score)",
        inputs=[],
    )
    assert DerivedMetricVersionSerializer(version).data["needs_rebuild"] is True
    version.level, version.kind, version.user_rule = "user", "count", "any"
    assert DerivedMetricVersionSerializer(version).data["needs_rebuild"] is False
