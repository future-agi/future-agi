"""Collector-only tag edits use the same scoped trace row as the Observe list."""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2 import trace_writer
from tracer.services.clickhouse.v2.trace_detail_reads import TraceDetailRead

pytestmark = [pytest.mark.django_db, pytest.mark.api]


def _trace_row(project_id, trace_id=None, **overrides):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    row = {
        "id": uuid.UUID(str(trace_id)) if trace_id else uuid.uuid4(),
        "project_id": uuid.UUID(str(project_id)),
        "project_version_id": None,
        "name": "collector trace",
        "session_id": uuid.uuid4(),
        "external_id": None,
        "tags": '["ingested"]',
        "metadata": '{"source":"collector"}',
        "input": '{"question":"hello"}',
        "output": '{"answer":"world"}',
        "error": "false",
        "error_analysis_status": "PENDING",
        "created_at": now,
        "updated_at": now,
        "is_deleted": 0,
        "_version": 1,
    }
    row.update(overrides)
    return row


@pytest.fixture
def tag_store(monkeypatch):
    """Mock only CH transport; real view, authorization, ORM and tag code run."""
    store = SimpleNamespace(rows=[], reads=[], writes=[], fail_write=False)

    def execute(_self, query, params, **kwargs):
        assert "FROM traces" in query
        assert "PREWHERE project_id IN %(tag_project_ids)s" in query
        assert "id = %(tag_trace_id)s" in query
        assert "GROUP BY project_id, id" in query
        assert "HAVING argMax(is_deleted, _version) = 0" in query
        assert "LIMIT 2" in query
        store.reads.append((query, params))
        scope = set(params["tag_project_ids"])
        latest = {}
        for row in store.rows:
            key = (str(row["project_id"]), str(row["id"]))
            if key[0] not in scope or key[1] != str(params["tag_trace_id"]):
                continue
            if key not in latest or row["_version"] > latest[key]["_version"]:
                latest[key] = row
        live = [row for row in latest.values() if not row["is_deleted"]][:2]
        if "AS trace_row" in query:
            return SimpleNamespace(
                data=[
                    {"trace_row": tuple(row[c] for c in trace_writer._TRACE_COLUMNS)}
                    for row in live
                ]
            )
        assert "argMax(tags, _version) AS tags" in query
        return SimpleNamespace(data=[{"tags": row["tags"]} for row in live])

    def insert(table, rows, column_names):
        assert table == "traces"
        assert tuple(column_names) == trace_writer._TRACE_COLUMNS
        if store.fail_write:
            raise RuntimeError("synthetic write unavailable")
        for values in rows:
            row = dict(zip(column_names, values, strict=True))
            store.writes.append(row)
            store.rows.append(row)

    writer = Mock()
    writer.insert.side_effect = insert
    monkeypatch.setattr(trace_writer, "_get_client", lambda: writer)
    monkeypatch.setattr(trace_writer, "_reset_client", lambda: None)
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.get_v2_query_client", Mock()
    )
    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService.execute_ch_query",
        execute,
    )
    return store


def _stub_detail_spans(monkeypatch, row):
    def read_trace_detail(*, project_ids, trace_id, **kwargs):
        assert str(row["project_id"]) in project_ids
        assert str(row["id"]) == trace_id
        span = {
            "id": "0123456789abcdef",
            "project_id": str(row["project_id"]),
            "trace_id": trace_id,
            "parent_span_id": None,
            "name": row["name"],
            "observation_type": "CHAIN",
            "start_time": row["created_at"],
            "end_time": row["created_at"],
            "input": row["input"],
            "output": row["output"],
            "status": "OK",
            "tags": '["root-span-tag"]',
            "metadata_json": row["metadata"],
            "span_attributes": "{}",
            "trace_session_id": str(row["session_id"]),
            "cost": 0,
            "total_tokens": 0,
        }
        return TraceDetailRead(
            project_id=str(row["project_id"]),
            spans=(span,),
            eval_config_ids=(),
            evals=(),
            annotations=(),
            query_count=2,
            elapsed_ms=0,
        )

    monkeypatch.setattr(
        "tracer.services.clickhouse.v2.trace_detail_reads.read_trace_detail",
        read_trace_detail,
    )


@pytest.mark.parametrize("tags", [["reviewed", "release"], []], ids=["add", "clear"])
def test_collector_tag_edit_round_trips_detail(
    auth_client, observe_project, tag_store, monkeypatch, tags
):
    row = _trace_row(observe_project.id)
    tag_store.rows.append(row)
    _stub_detail_spans(monkeypatch, row)
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()

    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/", {"tags": tags}, format="json"
    )

    assert response.status_code == 200
    assert response.data["result"] == {"id": str(row["id"]), "tags": tags}
    assert len(tag_store.writes) == 1
    saved = tag_store.writes[0]
    assert json.loads(saved["tags"]) == tags
    assert saved["_version"] > row["_version"]
    assert saved["updated_at"] >= row["updated_at"]
    for column in trace_writer._TRACE_COLUMNS:
        if column not in {"tags", "updated_at", "_version"}:
            assert saved[column] == row[column], column
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()

    detail = auth_client.get(
        f"/tracer/trace/{row['id']}/", {"project_id": str(observe_project.id)}
    )
    assert detail.status_code == 200
    assert detail.data["result"]["trace"]["tags"] == tags
    assert detail.data["result"]["trace"]["input"] == json.loads(row["input"])
    assert detail.data["result"]["trace"]["metadata"] == json.loads(row["metadata"])
    # The list uses argMax(tags, _version) on this same project/id in traces.
    latest = max(tag_store.rows, key=lambda item: item["_version"])
    assert json.loads(latest["tags"]) == tags


UI_TAG_CASES = [
    pytest.param(
        [
            {"name": "reviewed", "color": "#7857FC"},
            {"name": "release", "color": "#3B82F6"},
        ],
        [
            {"name": "reviewed", "color": "#7857FC"},
            {"name": "release", "color": "#3B82F6"},
        ],
        id="ui-objects",
    ),
    pytest.param(
        ["legacy", {"name": "reviewed", "color": "#7857FC"}, {"name": "release"}],
        ["legacy", {"name": "reviewed", "color": "#7857FC"}, {"name": "release"}],
        id="mixed",
    ),
    pytest.param(
        [
            {"name": " reviewed ", "color": "#7857FC"},
            "reviewed",
            {"name": "reviewed", "color": "#3B82F6"},
            " release ",
            {"name": "release"},
        ],
        [{"name": "reviewed", "color": "#7857FC"}, "release"],
        id="dedupe-by-name",
    ),
]


def _tag_names(tags):
    return [tag if isinstance(tag, str) else tag["name"] for tag in tags]


@pytest.mark.parametrize(("tags", "normalized"), UI_TAG_CASES)
def test_postgres_ui_tags_round_trip_detail(
    auth_client, trace, tag_store, monkeypatch, tags, normalized
):
    row = _trace_row(trace.project_id, trace.id)
    _stub_detail_spans(monkeypatch, row)
    before = (trace.name, trace.input, trace.output, trace.metadata, trace.project_id)

    response = auth_client.patch(
        f"/tracer/trace/{trace.id}/tags/", {"tags": tags}, format="json"
    )

    assert response.status_code == 200
    assert response.data["result"] == {"id": str(trace.id), "tags": normalized}
    trace.refresh_from_db()
    assert trace.tags == normalized
    assert (
        trace.name,
        trace.input,
        trace.output,
        trace.metadata,
        trace.project_id,
    ) == before
    assert tag_store.reads == []
    assert tag_store.writes == []

    detail = auth_client.get(
        f"/tracer/trace/{trace.id}/", {"project_id": str(trace.project_id)}
    )
    assert detail.status_code == 200
    assert detail.data["result"]["trace"]["tags"] == normalized
    assert _tag_names(detail.data["result"]["trace"]["tags"]) == _tag_names(normalized)


@pytest.mark.parametrize(("tags", "normalized"), UI_TAG_CASES)
def test_collector_ui_tags_round_trip_detail(
    auth_client, observe_project, tag_store, monkeypatch, tags, normalized
):
    row = _trace_row(observe_project.id)
    tag_store.rows.append(row)
    _stub_detail_spans(monkeypatch, row)
    names = _tag_names(normalized)

    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/", {"tags": tags}, format="json"
    )

    assert response.status_code == 200
    assert response.data["result"] == {"id": str(row["id"]), "tags": normalized}
    assert len(tag_store.writes) == 1
    saved = tag_store.writes[0]
    assert json.loads(saved["tags"]) == names
    assert saved["_version"] > row["_version"]
    for column in trace_writer._TRACE_COLUMNS:
        if column not in {"tags", "updated_at", "_version"}:
            assert saved[column] == row[column], column
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()

    detail = auth_client.get(
        f"/tracer/trace/{row['id']}/", {"project_id": str(observe_project.id)}
    )
    assert detail.status_code == 200
    assert detail.data["result"]["trace"]["tags"] == names
    assert _tag_names(detail.data["result"]["trace"]["tags"]) == names


@pytest.mark.parametrize("backend", ["postgres", "collector"])
@pytest.mark.parametrize(
    "tags",
    [
        pytest.param([23], id="number"),
        pytest.param([True], id="boolean"),
        pytest.param([None], id="null-tag"),
        pytest.param([["nested"]], id="list-tag"),
        pytest.param([""], id="empty-string"),
        pytest.param(["  "], id="blank-string"),
        pytest.param([{}], id="empty-object"),
        pytest.param([{"name": "  "}], id="blank-name"),
        pytest.param([{"name": 23}], id="numeric-name"),
        pytest.param([{"name": "reviewed", "color": None}], id="null-color"),
        pytest.param([{"name": "reviewed", "color": 23}], id="numeric-color"),
        pytest.param([{"name": "reviewed", "extra": "no"}], id="unknown-key"),
        pytest.param(
            ["reviewed", {"name": "reviewed", "color": False}],
            id="invalid-duplicate",
        ),
        pytest.param(None, id="null-list"),
        pytest.param("reviewed", id="non-list"),
    ],
)
def test_invalid_tag_shapes_rejected_without_mutation(
    auth_client, trace, observe_project, tag_store, backend, tags
):
    row = _trace_row(observe_project.id)
    tag_store.rows.append(row)
    trace_id = trace.id if backend == "postgres" else row["id"]
    original_tags = list(trace.tags)
    response = auth_client.patch(
        f"/tracer/trace/{trace_id}/tags/", {"tags": tags}, format="json"
    )
    assert response.status_code == 400
    assert "tags" in str(response.data).lower()
    if isinstance(tags, list):
        assert "non-empty string" in str(response.data)
    trace.refresh_from_db()
    assert trace.tags == original_tags
    assert tag_store.reads == []
    assert tag_store.writes == []
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()


def test_detail_prefers_trace_tags_but_preserves_missing_dimension_fallback(
    auth_client, observe_project, tag_store, monkeypatch
):
    row = _trace_row(observe_project.id)
    _stub_detail_spans(monkeypatch, row)
    path = f"/tracer/trace/{row['id']}/"
    query = {"project_id": str(observe_project.id)}
    missing = auth_client.get(path, query)
    assert missing.status_code == 200
    assert missing.data["result"]["trace"]["tags"] == ["root-span-tag"]

    tag_store.rows.append(row)
    present = auth_client.get(path, query)
    assert present.status_code == 200
    assert present.data["result"]["trace"]["tags"] == ["ingested"]


@pytest.mark.parametrize("foreign_scope", ["organization", "workspace"])
def test_collector_trace_outside_scope_cannot_be_tagged(
    auth_client, organization, user, observe_project, tag_store, foreign_scope
):
    foreign_org = (
        Organization.objects.create(name="Other synthetic organization")
        if foreign_scope == "organization"
        else organization
    )
    foreign_workspace = Workspace.no_workspace_objects.create(
        name="Other synthetic workspace", organization=foreign_org, created_by=user
    )
    foreign_project = Project.no_workspace_objects.create(
        name="Other synthetic project",
        organization=foreign_org,
        workspace=foreign_workspace,
        trace_type="observe",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
    )
    row = _trace_row(foreign_project.id)
    tag_store.rows.append(row)

    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/",
        {"tags": ["reviewed"]},
        format="json",
    )

    assert response.status_code in {400, 404}
    assert tag_store.writes == []
    assert all(
        str(foreign_project.id) not in params["tag_project_ids"]
        for _, params in tag_store.reads
    )
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()


def test_collector_project_pin_is_honored(auth_client, observe_project, tag_store):
    row = _trace_row(observe_project.id)
    tag_store.rows.append(row)
    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/?project_id={uuid.uuid4()}",
        {"tags": ["reviewed"]},
        format="json",
    )
    assert response.status_code in {400, 404}
    assert tag_store.reads == []
    assert tag_store.writes == []


def test_collector_ambiguous_id_requires_project_pin(
    auth_client, observe_project, tag_store
):
    other = Project.no_workspace_objects.create(
        name="Second accessible project",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        trace_type="observe",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
    )
    row = _trace_row(observe_project.id)
    tag_store.rows.extend([row, _trace_row(other.id, row["id"])])
    path = f"/tracer/trace/{row['id']}/tags/"
    ambiguous = auth_client.patch(path, {"tags": ["reviewed"]}, format="json")
    assert ambiguous.status_code in {400, 404}
    assert tag_store.writes == []

    pinned = auth_client.patch(
        f"{path}?project_id={observe_project.id}",
        {"tags": ["reviewed"]},
        format="json",
    )
    assert pinned.status_code == 200
    assert len(tag_store.writes) == 1
    assert str(tag_store.writes[0]["project_id"]) == str(observe_project.id)


def test_collector_project_pin_does_not_edit_another_projects_pg_trace(
    auth_client, observe_project, trace, tag_store
):
    assert trace.project_id != observe_project.id
    original_tags = list(trace.tags)
    tag_store.rows.append(_trace_row(observe_project.id, trace.id))
    response = auth_client.patch(
        f"/tracer/trace/{trace.id}/tags/?project_id={observe_project.id}",
        {"tags": ["reviewed"]},
        format="json",
    )
    assert response.status_code == 200
    assert len(tag_store.writes) == 1
    assert str(tag_store.writes[0]["project_id"]) == str(observe_project.id)
    trace.refresh_from_db()
    assert trace.tags == original_tags


def test_collector_tombstone_is_not_revived(auth_client, observe_project, tag_store):
    row = _trace_row(observe_project.id)
    tag_store.rows.extend([row, {**row, "_version": 2, "is_deleted": 1}])
    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/", {"tags": ["reviewed"]}, format="json"
    )
    assert response.status_code in {400, 404}
    assert tag_store.writes == []


def test_collector_write_failure_is_not_reported_as_success(
    auth_client, observe_project, tag_store
):
    row = _trace_row(observe_project.id)
    tag_store.rows.append(row)
    tag_store.fail_write = True
    response = auth_client.patch(
        f"/tracer/trace/{row['id']}/tags/", {"tags": ["reviewed"]}, format="json"
    )
    assert response.status_code == 400
    assert tag_store.writes == []
    assert "synthetic write unavailable" not in str(response.data)
    assert not Trace.no_workspace_objects.filter(id=row["id"]).exists()


def test_postgres_tag_edit_still_works_without_clickhouse(
    auth_client, trace, tag_store
):
    before = (trace.name, trace.input, trace.output, trace.metadata, trace.project_id)
    response = auth_client.patch(
        f"/tracer/trace/{trace.id}/tags/", {"tags": ["reviewed"]}, format="json"
    )
    assert response.status_code == 200
    assert response.data["result"] == {"id": str(trace.id), "tags": ["reviewed"]}
    trace.refresh_from_db()
    assert trace.tags == ["reviewed"]
    assert (
        trace.name,
        trace.input,
        trace.output,
        trace.metadata,
        trace.project_id,
    ) == before
    assert tag_store.reads == []
    assert tag_store.writes == []
