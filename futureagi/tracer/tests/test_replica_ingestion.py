"""Ingestion must read its own writes, even while opted-in replicas lag.

Only outbound ClickHouse calls are stubbed. All reads and writes use the real
router and the frozen PostgreSQL connection from ``lagging_replica``.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from django.db import connections, transaction

from integrations.transformers.langfuse_transformer import LangfuseTransformer
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.deterministic_id import (
    deterministic_trace_session_id,
)
from tracer.utils.create_otel_span import create_single_otel_span
from tracer.utils.langfuse_upsert import upsert_langfuse_trace

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


@pytest.fixture
def mirror_client(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "true")
    client = MagicMock()
    monkeypatch.setattr(trace_writer, "_get_client", lambda: client)
    return client


def _mirrored_row(client):
    client.insert.assert_called_once()
    args, kwargs = client.insert.call_args
    assert args[0] == "traces"
    assert len(args[1]) == 1
    return dict(zip(kwargs["column_names"], args[1][0], strict=True))


def test_new_trace_mirrors_once_after_commit(
    lagging_replica,  # noqa: F811
    observe_project,
    mirror_client,
):
    """C46: a trace not yet on the replica must still reach ClickHouse."""
    lagging_replica.freeze()
    with transaction.atomic():
        trace = Trace.objects.create(project=observe_project, name="new trace")
        transaction.on_commit(
            lambda: trace_writer.mirror_traces_to_clickhouse([trace.id])
        )
        mirror_client.insert.assert_not_called()

    row = _mirrored_row(mirror_client)
    assert row["id"] == str(trace.id)
    assert row["name"] == "new trace"
    assert row["is_deleted"] == 0


@pytest.mark.parametrize("deleted", [False, True])
def test_updated_trace_mirrors_current_fields_and_tombstone(
    lagging_replica,  # noqa: F811
    observe_project,
    mirror_client,
    deleted,
):
    """C46: do not mirror stale fields or lose an all_objects tombstone."""
    trace = Trace.objects.create(project=observe_project, name="old", input="old input")
    lagging_replica.freeze()
    session_id = uuid.uuid4()
    with transaction.atomic():
        trace.name = "updated"
        trace.input = {"question": "latest"}
        trace.output = "latest output"
        trace.session_id = session_id
        trace.deleted = deleted
        trace.save()
        transaction.on_commit(
            lambda: trace_writer.mirror_traces_to_clickhouse([trace.id])
        )
        mirror_client.insert.assert_not_called()

    row = _mirrored_row(mirror_client)
    assert row["name"] == "updated"
    assert json.loads(row["input"]) == {"question": "latest"}
    assert row["output"] == "latest output"
    assert row["session_id"] == str(session_id)
    assert row["is_deleted"] == int(deleted)


def _otel_payload(project, trace_id, *, parent_id=None, session=None):
    attrs = {
        "fi.span.kind": "CHAIN",
        "input.value": "root input",
        "output.value": "root output",
    }
    if session:
        attrs["session.id"] = session
    return {
        "trace_id": str(trace_id),
        "span_id": uuid.uuid4().hex,
        "parent_id": parent_id,
        "name": "root" if parent_id is None else "child",
        "start_time": 1_700_000_000_000_000_000,
        "end_time": 1_700_000_001_000_000_000,
        "attributes": attrs,
        "events": [],
        "status": "OK",
        "project_name": project.name,
        "project_type": "observe",
    }


def test_child_export_cannot_revert_root_io_or_session_in_executor(
    lagging_replica,  # noqa: F811
    observe_project,
    user,
):
    """C51 get: executor-local reads must see the preceding root export."""
    project = observe_project
    trace = Trace.objects.create(
        project=project, input="old input", output="old output"
    )
    root = _otel_payload(project, trace.id, session="fresh-session")
    child = _otel_payload(project, trace.id, parent_id=root["span_id"])

    def export_in_worker():
        # Connections and their snapshots are thread-local, just like ingestion.
        lagging_replica.connection.connect()
        try:
            lagging_replica.freeze()
            for payload in (root, child):
                create_single_otel_span(
                    payload,
                    str(project.organization_id),
                    str(user.id),
                    str(project.workspace_id),
                )
        finally:
            lagging_replica.thaw()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(export_in_worker).result(timeout=30)

    current = Trace.objects.using("default").get(id=trace.id)
    assert current.input == "root input"
    assert current.output == "root output"
    assert current.session_id == deterministic_trace_session_id(
        project.id, "fresh-session"
    )
    assert ObservationSpan.objects.using("default").filter(trace=trace).count() == 2


def test_new_foreign_project_trace_is_rejected_before_export(
    lagging_replica,  # noqa: F811
    observe_project,
    project,
    user,
):
    """C51 identity guard: a new trace in another project is not invisible."""
    lagging_replica.freeze()
    foreign_trace = Trace.objects.create(project=project)
    payload = _otel_payload(observe_project, foreign_trace.id)
    with pytest.raises(
        ValueError, match="Trace does not belong to the resolved project"
    ):
        create_single_otel_span(
            payload,
            str(observe_project.organization_id),
            str(user.id),
            str(observe_project.workspace_id),
        )
    assert (
        not ObservationSpan.objects.using("default")
        .filter(id=payload["span_id"])
        .exists()
    )


def _upsert(project, payload):
    return upsert_langfuse_trace(
        assembled_trace=payload,
        transformer=LangfuseTransformer(),
        project_id=str(project.id),
        org=project.organization,
        workspace=project.workspace,
        org_id=project.organization_id,
    )


def test_repeated_langfuse_external_id_reuses_new_trace(
    lagging_replica,  # noqa: F811
    observe_project,
    project,
):
    """C57: reuse a trace created after the replica froze, within its project."""
    unrelated = Trace.objects.create(
        project=project, external_id="repeat", name="other project"
    )
    lagging_replica.freeze()
    assert _upsert(observe_project, {"id": "repeat", "name": "first"}) == (True, 0, 0)
    first = Trace.objects.using("default").get(
        project=observe_project, external_id="repeat"
    )
    assert _upsert(observe_project, {"id": "repeat", "name": "second"}) == (False, 0, 0)
    traces = Trace.objects.using("default").filter(
        project=observe_project, external_id="repeat"
    )
    assert traces.count() == 1
    assert traces.get().id == first.id
    assert traces.get().name == "second"
    assert Trace.objects.using("default").get(id=unrelated.id).name == "other project"


def test_langfuse_root_timing_includes_new_child_and_prior_batches(
    lagging_replica,  # noqa: F811
    observe_project,
):
    """C58: the in-transaction timing aggregate needs old AND new children."""
    trace = Trace.objects.create(project=observe_project, external_id="timing")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    ObservationSpan.objects.create(
        id="prior-child",
        trace=trace,
        project=observe_project,
        name="prior",
        observation_type="chain",
        start_time=start,
        end_time=start + timedelta(seconds=1),
        parent_span_id="root-timing",
    )
    # Identity already replicated: this failure is independent of C57.
    lagging_replica.freeze()
    with transaction.atomic():
        result = _upsert(
            observe_project,
            {
                "id": "timing",
                "observations": [
                    {
                        "id": "new-child",
                        "type": "SPAN",
                        "name": "new",
                        "startTime": (start + timedelta(seconds=2)).isoformat(),
                        "endTime": (start + timedelta(seconds=4.5)).isoformat(),
                    }
                ],
            },
        )
        assert result == (False, 1, 0)
        root = ObservationSpan.objects.using("default").get(id="root-timing")
        assert root.start_time == start
        assert root.end_time == start + timedelta(seconds=4.5)
        assert root.latency_ms == 4500


def test_langfuse_same_batch_observation_score_is_persisted(
    lagging_replica,  # noqa: F811
    observe_project,
):
    """C59: scores must find observations upserted in the same atomic block."""
    trace = Trace.objects.create(project=observe_project, external_id="obs-score")
    lagging_replica.freeze()
    result = _upsert(
        observe_project,
        {
            "id": "obs-score",
            "observations": [{"id": "scored-child", "type": "SPAN", "name": "child"}],
            "scores": [
                {
                    "id": "child-score",
                    "observationId": "scored-child",
                    "name": "quality",
                    "value": 0.75,
                }
            ],
        },
    )
    assert result == (False, 1, 1)
    score = EvalLogger.objects.using("default").get(eval_id="child-score")
    assert score.trace_id == trace.id
    assert score.observation_span_id == "scored-child"
    assert score.output_float == 0.75


def test_langfuse_trace_score_links_newly_created_root(
    lagging_replica,  # noqa: F811
    observe_project,
):
    """C60: the fallback finds the synthetic root, even without observations."""
    trace = Trace.objects.create(project=observe_project, external_id="trace-score")
    lagging_replica.freeze()
    result = _upsert(
        observe_project,
        {
            "id": "trace-score",
            "scores": [{"id": "root-score", "name": "quality", "value": 1}],
        },
    )
    assert result == (False, 0, 1)
    score = EvalLogger.objects.using("default").get(eval_id="root-score")
    assert score.trace_id == trace.id
    assert score.observation_span_id == "root-trace-score"
    assert score.output_float == 1
