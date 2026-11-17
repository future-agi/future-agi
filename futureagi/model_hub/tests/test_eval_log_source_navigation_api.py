"""TH-4804 I01-I27: opt-in API, scope fences, and real bounded CH reads."""

import inspect
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.evals_metric import EvalTemplate, Feedback
from model_hub.models.score import Score
from model_hub.serializers.contracts import EvalApiLogRowResponseSerializer
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.trace_detail_reads import (
    TraceDetailRead,
    TraceDetailReadUnavailable,
)
from tracer.tests import conftest as tracer_fixtures
from tracer.tests._ch_seed import _pg_row_from_django_span, seed_ch_span

# Register the shared tracer fixtures in this sibling test module.
ch_seed = tracer_fixtures.ch_seed
child_span = tracer_fixtures.child_span
custom_eval_config = tracer_fixtures.custom_eval_config
eval_template = tracer_fixtures.eval_template
observation_span = tracer_fixtures.observation_span
project = tracer_fixtures.project
project_version = tracer_fixtures.project_version
trace = tracer_fixtures.trace

usage_models = pytest.importorskip("ee.usage.models.usage", reason="requires ee/")
APICallLog = usage_models.APICallLog
pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]
URL = "/model-hub/get-eval-logs"
NAV_MODULE = "model_hub.services.eval_log_source_navigation"


@pytest.fixture
def log_factory(user, workspace, project, custom_eval_config):
    def create(*, extra=None, stamp=True, row_source="tracer", **fields):
        config = {
            "source": "tracer",
            "custom_eval_config_id": str(custom_eval_config.id),
            "trace_id": str(uuid4()),
            "span_id": str(uuid4()),
            "required_keys": ["output"],
            "mappings": {"output": "stored input"},
            "output": {"output": 0.75, "reason": "stored reason"},
            "input_data_types": {"output": "text"},
        }
        if stamp:
            config["project_id"] = str(project.id)
        config.update(extra or {})
        return APICallLog.no_workspace_objects.create(
            organization=user.organization,
            workspace=workspace,
            cost=0,
            status="success",
            source=row_source,
            source_id=str(custom_eval_config.eval_template_id),
            config=json.dumps(config),
            **fields,
        )

    return create


def _get(client, log, opt_in="true", **query):
    params = {"log_id": str(log.log_id), "order": "", "source": "logs", **query}
    if opt_in is not None:
        params["include_source_navigation"] = opt_in
    return client.get(URL, params)


def _nav(response, status="ready"):
    assert response.status_code == 200, response.content
    nav = response.json()["result"]["source_navigation"]
    assert nav["status"] == status
    assert set(nav) == {
        "status",
        "kind",
        "project_id",
        "trace_id",
        "span_id",
        "retryable",
    }
    if status != "ready":
        assert all(
            nav[key] is None for key in ("kind", "project_id", "trace_id", "span_id")
        )
    return nav


@pytest.fixture
def fake_reader(monkeypatch):
    def read(**kwargs):
        return TraceDetailRead(
            kwargs["project_ids"][0],
            (
                {
                    "id": read.span_id,
                    "project_id": kwargs["project_ids"][0],
                    "trace_id": kwargs["trace_id"],
                    "parent_span_id": None,
                    "observation_type": "llm",
                },
            ),
            (),
            (),
            (),
            2,
            1.0,
        )

    reader = Mock(side_effect=read)

    def bind(log):
        read.span_id = json.loads(log.config)["span_id"]
        return reader

    monkeypatch.setattr(f"{NAV_MODULE}.read_trace_detail", reader)
    bind.reader = reader
    return bind


def test_i01_omitted_opt_in_matches_original_payload(
    auth_client, log_factory, monkeypatch
):
    log = log_factory()
    config = json.loads(log.config)
    # This exact snapshot is also run against the unmodified view before wiring.
    expected = {
        "status": True,
        "result": {
            "log_id": str(log.log_id),
            "evaluation_id": str(log.log_id),
            "created_at": log.created_at.isoformat().replace("+00:00", "Z"),
            "source": "Tracer",
            "required_keys": config["required_keys"],
            "values": config["mappings"],
            "output": config["output"],
            "input_data_types": config["input_data_types"],
            "span_id": config["span_id"],
            "trace_id": config["trace_id"],
        },
    }
    response = _get(auth_client, log, None)
    assert response.status_code == 200
    assert response.json() == expected


def test_i02_false_opt_in_preserves_payload(auth_client, log_factory, fake_reader):
    log = log_factory()
    reader = fake_reader(log)
    assert (
        _get(auth_client, log, "false").content == _get(auth_client, log, None).content
    )
    reader.assert_not_called()


@pytest.mark.parametrize("value", ["yes", "1"])
def test_i03_invalid_opt_in(auth_client, log_factory, value):
    response = _get(auth_client, log_factory(), value)
    assert response.status_code == 400
    assert "include_source_navigation" in response.json()["result"]


def test_i04_dataset_enrichment_preserves_payload(auth_client, log_factory):
    log = log_factory(extra={"source": "dataset"}, row_source="dataset")
    before = _get(auth_client, log, None).json()
    response = _get(auth_client, log)
    _nav(response, "unsupported_source")
    after = response.json()
    del after["result"]["source_navigation"]
    assert before == after


@pytest.fixture
def collector_tree(observation_span, child_span, ch_seed, settings):
    # The isolated stack ships the direct-write eval table, not the CDC table.
    settings.CH25_EVAL_LOGGER_TABLE = "tracer_eval_logger_v2"
    # Collector converter.go preserves the 16-character OTel SpanID string.
    # Keep that wire identity through ClickHouse, usage config and navigation.
    root = _pg_row_from_django_span(observation_span)
    child = _pg_row_from_django_span(child_span)
    root["id"], child["id"] = uuid4().hex[:16], uuid4().hex[:16]
    child["parent_span_id"] = root["id"]
    seed_ch_span(root)
    seed_ch_span(child)
    return root, child


def _tree_log(log_factory, collector_tree, **kwargs):
    root, child = collector_tree
    return log_factory(
        extra={"trace_id": str(root["trace_id"]), "span_id": child["id"]}, **kwargs
    )


def test_i05_real_clickhouse_trace(auth_client, log_factory, collector_tree, project):
    nav = _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))
    assert nav["kind"] == "trace"
    assert nav["project_id"] == str(project.id)
    assert nav["span_id"] == collector_tree[1]["id"]


@pytest.mark.parametrize("opt_in", [None, "true"])
def test_i05c_collector_span_id_matches_declared_response(
    auth_client, log_factory, fake_reader, opt_in
):
    # Observe logs store the collector's 16-hex OTel span id. The declared
    # response (and the frontend contract generated from it) must accept it.
    log = log_factory(extra={"span_id": uuid4().hex[:16]})
    fake_reader(log)
    response = _get(auth_client, log, opt_in)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["result"]["span_id"] == json.loads(log.config)["span_id"]
    declared = EvalApiLogRowResponseSerializer(data=body)
    assert declared.is_valid(), declared.errors


def test_i05b_trace_destination_nests_the_evaluated_child(
    auth_client, log_factory, collector_tree
):
    # View trace (R1-02) looks for exactly one span with the pinned id, trace
    # and project anywhere in the tree the trace drawer reads. Hold the real
    # response to that shape: the evaluated collector child is nested.
    root, child = collector_tree
    nav = _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))
    response = auth_client.get(
        f"/tracer/trace/{nav['trace_id']}/", {"project_id": nav["project_id"]}
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert (result["trace"]["id"], result["trace"]["project"]) == (
        nav["trace_id"],
        nav["project_id"],
    )
    top_level = {
        entry["observation_span"]["id"] for entry in result["observation_spans"]
    }
    assert root["id"] in top_level and child["id"] not in top_level
    matches, pending = [], list(result["observation_spans"])
    while pending:
        entry = pending.pop()
        span = entry["observation_span"]
        if span["id"] == nav["span_id"]:
            matches.append((span["trace"], span["project"]))
        pending.extend(entry.get("children") or [])
    assert matches == [(nav["trace_id"], nav["project_id"])]


def test_i06_clickhouse_only_without_postgres_trace(
    auth_client, log_factory, collector_tree
):
    root, child = collector_tree
    trace_id = str(uuid4())
    for row in (root, child):
        row["trace_id"] = trace_id
        seed_ch_span(row)
    assert not Trace.no_workspace_objects.filter(id=trace_id).exists()
    assert (
        _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))["kind"]
        == "trace"
    )


def test_i07_real_voice_destination(auth_client, log_factory, collector_tree, project):
    root, _ = collector_tree
    root["observation_type"] = "conversation"
    root["parent_span_id"] = ""
    provider_id = "provider-id-distinct-from-trace-and-log"
    root["provider"] = "vapi"
    root["span_attributes"] = {"raw_log": {"id": provider_id}}
    seed_ch_span(root)
    log = _tree_log(log_factory, collector_tree)
    nav = _nav(_get(auth_client, log))
    assert nav["kind"] == "voice_call"
    assert nav["trace_id"] == str(root["trace_id"])
    response = auth_client.get(
        "/tracer/trace/voice_call_detail/",
        {
            "trace_id": nav["trace_id"],
            "project_id": nav["project_id"],
        },
    )
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["project_id"] == nav["project_id"]
    assert result["trace_id"] == nav["trace_id"]
    assert result["id"] == nav["trace_id"]
    assert result["provider_call_id"] == provider_id
    assert provider_id not in (nav["trace_id"], str(log.log_id))
    assert not any("simulation" in key for key in result)

    # The host's destination read proves the evaluated collector span in the
    # same snapshot it renders (R1-04/R2-03), through the real serializer.
    proven = auth_client.get(
        "/tracer/trace/voice_call_detail/",
        {
            "trace_id": nav["trace_id"],
            "project_id": nav["project_id"],
            "expected_span_id": nav["span_id"],
        },
    )
    assert proven.status_code == 200, proven.content
    spans = proven.json()["result"]["observation_span"]
    assert [span["id"] for span in spans if span["id"] == nav["span_id"]] == [
        collector_tree[1]["id"]
    ]


def test_i07b_voice_destination_refuses_unproven_expected_span(
    auth_client, log_factory, collector_tree
):
    root, child = collector_tree
    root["observation_type"] = "conversation"
    root["parent_span_id"] = ""
    seed_ch_span(root)
    nav = _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))
    url = "/tracer/trace/voice_call_detail/"
    base = {"trace_id": nav["trace_id"], "project_id": nav["project_id"]}

    # A span that is not in this call's tree never yields call content.
    missing = auth_client.get(url, {**base, "expected_span_id": "0" * 16})
    assert missing.status_code == 409, missing.content
    assert "observation_span" not in missing.json().get("result", {})

    # A second conversation root makes the call ambiguous for this span.
    seed_ch_span(
        {
            **child,
            "id": uuid4().hex[:16],
            "parent_span_id": "",
            "observation_type": "conversation",
        }
    )
    ambiguous = auth_client.get(url, {**base, "expected_span_id": nav["span_id"]})
    assert ambiguous.status_code == 409, ambiguous.content

    # The proof is pinned to an Observe project and malformed IDs are rejected.
    unpinned = auth_client.get(
        url, {"trace_id": nav["trace_id"], "expected_span_id": nav["span_id"]}
    )
    assert unpinned.status_code == 400, unpinned.content
    padded = auth_client.get(url, {**base, "expected_span_id": f" {nav['span_id']}"})
    assert padded.status_code == 400, padded.content


def test_i08_colliding_newer_project_is_never_selected(
    auth_client, log_factory, collector_tree, project, monkeypatch
):
    from model_hub.services import eval_log_source_navigation as navigation

    other = Project.no_workspace_objects.create(
        name="Newer copy",
        organization=project.organization,
        workspace=project.workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    for row in collector_tree:
        seed_ch_span(
            {
                **row,
                "project_id": str(other.id),
                "start_time": timezone.now() + timedelta(seconds=1),
            }
        )
    reader = Mock(wraps=navigation.read_trace_detail)
    monkeypatch.setattr(navigation, "read_trace_detail", reader)
    nav = _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))
    assert nav["project_id"] == str(project.id)
    assert reader.call_args.kwargs["project_ids"] == [str(project.id)]
    assert reader.call_count == 1


def _sibling_project(project, name):
    return Project.no_workspace_objects.create(
        name=name,
        organization=project.organization,
        workspace=project.workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )


def _seed_copy(collector_tree, target, *, trace_id=None):
    """Write the collector tree into ``target``, optionally under a new trace id."""
    for row in collector_tree:
        seed_ch_span(
            {
                **row,
                "project_id": str(target.id),
                "org_id": str(target.organization_id),
                "trace_id": trace_id or row["trace_id"],
            }
        )


@pytest.fixture
def wrapped_reader(monkeypatch):
    from model_hub.services import eval_log_source_navigation as navigation

    reader = Mock(wraps=navigation.read_trace_detail)
    monkeypatch.setattr(navigation, "read_trace_detail", reader)
    return reader


def test_i09_stamped_project_wins_over_reassigned_config(
    auth_client, log_factory, custom_eval_config, project, fake_reader
):
    log = log_factory()
    other = _sibling_project(project, "Reassigned")
    CustomEvalConfig.all_objects.filter(id=custom_eval_config.id).update(project=other)
    fake_reader(log)
    nav = _nav(_get(auth_client, log))
    assert nav["project_id"] == str(project.id)
    assert fake_reader.reader.call_args.kwargs["project_ids"] == [str(project.id)]


NOT_RECORDED = "source_not_recorded"


@pytest.fixture
def clickhouse_queries(monkeypatch):
    """Every ClickHouse query source navigation sends, in order."""
    from model_hub.services import eval_log_source_navigation as navigation

    analytics = navigation.V2AnalyticsQueryService()
    query = Mock(wraps=analytics.execute_ch_query)
    monkeypatch.setattr(analytics, "execute_ch_query", query)
    monkeypatch.setattr(navigation, "V2AnalyticsQueryService", lambda: analytics)
    return query


def _older_log(log_factory, collector_tree, trace_id=None):
    """An eval log written before logs recorded their project."""
    root, child = collector_tree
    return log_factory(
        stamp=False,
        extra={"trace_id": trace_id or str(root["trace_id"]), "span_id": child["id"]},
    )


def test_i10_older_log_gets_no_navigation_and_reads_no_trace(
    auth_client,
    log_factory,
    collector_tree,
    custom_eval_config,
    project,
    wrapped_reader,
    clickhouse_queries,
):
    """TH-4804 r9: an older log never recorded the project it ran on, so no
    lookup can name its source. Even with its trace live in exactly one
    readable project it gets "source not recorded", before any trace read."""
    log = _older_log(log_factory, collector_tree)
    moved = _sibling_project(project, "Config moved here")
    CustomEvalConfig.all_objects.filter(id=custom_eval_config.id).update(project=moved)
    response = _get(auth_client, log)
    assert _nav(response, NOT_RECORDED)["retryable"] is False
    wrapped_reader.assert_not_called()
    clickhouse_queries.assert_not_called()
    body = response.content.decode()
    assert str(project.id) not in body and str(moved.id) not in body
    # The evaluation itself is unchanged.
    assert response.json()["result"]["output"] == json.loads(log.config)["output"]


def test_i10b_older_log_with_a_tombstoned_original_and_a_live_replay(
    auth_client,
    log_factory,
    collector_tree,
    project,
    wrapped_reader,
    clickhouse_queries,
):
    """The gate's tombstone case: readable project A holds the original trace,
    tombstoned; readable project B holds a live replay with the same ids. The
    older log must not resolve to B."""
    replay = _sibling_project(project, "Readable replay")
    trace_id = str(uuid4())
    _seed_copy(collector_tree, project, trace_id=trace_id)
    _seed_copy(collector_tree, replay, trace_id=trace_id)
    _seed_trace_rows(trace_id, project, replay)
    _seed_trace_rows(trace_id, project, is_deleted=1)
    log = _older_log(log_factory, collector_tree, trace_id)
    response = _get(auth_client, log)
    _nav(response, NOT_RECORDED)
    wrapped_reader.assert_not_called()
    clickhouse_queries.assert_not_called()
    body = response.content.decode()
    assert str(project.id) not in body and str(replay.id) not in body
    assert replay.name not in body


def test_i10c_older_log_answer_ignores_a_trace_in_an_inaccessible_workspace(
    auth_client, log_factory, collector_tree, user, project, clickhouse_queries
):
    """The gate's cross-workspace case: a same-id trace in a workspace the
    caller cannot access changes nothing in the response, and no query
    touches a project outside the caller's readable set (none runs at all)."""
    trace_id = str(uuid4())
    _seed_copy(collector_tree, project, trace_id=trace_id)
    _seed_trace_rows(trace_id, project)
    log = _older_log(log_factory, collector_tree, trace_id)
    before = _get(auth_client, log)
    _nav(before, NOT_RECORDED)

    # A workspace this user is no member of, in the same organization.
    hidden_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid4()),
        organization=user.organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    hidden = Project.no_workspace_objects.create(
        name="Inaccessible workspace project",
        organization=project.organization,
        workspace=hidden_workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    _seed_copy(collector_tree, hidden, trace_id=trace_id)
    _seed_trace_rows(trace_id, hidden)
    after = _get(auth_client, log)
    assert after.status_code == before.status_code == 200
    assert after.json() == before.json()
    clickhouse_queries.assert_not_called()
    assert str(hidden.id) not in after.content.decode()


@pytest.mark.parametrize("original", ["deleted", "other_workspace", "tombstoned"])
def test_i10e_older_log_replay_of_an_unreadable_original_is_not_recorded(
    auth_client,
    log_factory,
    collector_tree,
    user,
    workspace,
    project,
    original,
    clickhouse_queries,
):
    """The replay scenario of task 845, under the r9 rule: no link, and
    neither project is named."""
    replay = _sibling_project(project, "Readable replay")
    other_workspace = _workspace(user) if original == "other_workspace" else None
    source = Project.no_workspace_objects.create(
        name="Original source",
        organization=project.organization,
        workspace=other_workspace or project.workspace,
        model_type=project.model_type,
        trace_type=project.trace_type,
    )
    trace_id = str(uuid4())
    _seed_copy(collector_tree, source, trace_id=trace_id)
    _seed_copy(collector_tree, replay, trace_id=trace_id)
    _seed_trace_rows(trace_id, source, replay)
    if original == "tombstoned":
        _seed_trace_rows(trace_id, source, is_deleted=1)
    if original in ("deleted", "tombstoned"):
        Project.no_workspace_objects.filter(id=source.id).update(deleted=True)
    log = _older_log(log_factory, collector_tree, trace_id)
    response = _get(auth_client, log)
    _nav(response, NOT_RECORDED)
    clickhouse_queries.assert_not_called()
    body = response.content.decode()
    assert str(source.id) not in body and str(replay.id) not in body
    assert source.name not in body


def test_i10f_a_recorded_log_beside_it_still_resolves(
    auth_client, log_factory, collector_tree, project
):
    """The same trace and span: recorded, it opens; unrecorded, it does not."""
    root, child = collector_tree
    older = _older_log(log_factory, collector_tree)
    recorded = _tree_log(log_factory, collector_tree)
    _nav(_get(auth_client, older), NOT_RECORDED)
    nav = _nav(_get(auth_client, recorded))
    assert (nav["project_id"], nav["trace_id"], nav["span_id"]) == (
        str(project.id),
        str(root["trace_id"]),
        child["id"],
    )


def _seed_trace_rows(trace_id, *projects, is_deleted=0):
    """The direct-write ``traces`` rows (ORDER BY project_id, id) of a trace."""
    from tracer.tests._ch_seed import _get_ch_client

    client = _get_ch_client()
    try:
        client.insert(
            "traces",
            [
                [trace_id, str(project.id), is_deleted, "2026-01-01 00:00:00"]
                for project in projects
            ],
            column_names=["id", "project_id", "is_deleted", "created_at"],
        )
    finally:
        client.close()


@pytest.mark.parametrize("change", ["config_soft_deleted", "config_gone"])
def test_i11_older_log_after_its_config_changes_is_not_recorded(
    auth_client, log_factory, collector_tree, custom_eval_config, change
):
    log = _older_log(log_factory, collector_tree)
    configs = CustomEvalConfig.all_objects.filter(id=custom_eval_config.id)
    if change == "config_gone":
        configs.delete()
    else:
        configs.update(deleted=True)
    _nav(_get(auth_client, log), NOT_RECORDED)


def test_i13_deleted_project(auth_client, log_factory, project, fake_reader):
    log = log_factory()
    Project.no_workspace_objects.filter(id=project.id).update(deleted=True)
    fake_reader(log)
    _nav(_get(auth_client, log), "unavailable")
    fake_reader.reader.assert_not_called()


def test_i14_other_organization_project(auth_client, log_factory, project, fake_reader):
    log = log_factory()
    other = Organization.objects.create(name="Secret other organization")
    Project.no_workspace_objects.filter(id=project.id).update(organization=other)
    fake_reader(log)
    response = _get(auth_client, log)
    _nav(response, "unavailable")
    assert project.name not in response.content.decode()
    fake_reader.reader.assert_not_called()


def _workspace(user, *, default=False):
    return Workspace.no_workspace_objects.create(
        name=str(uuid4()),
        organization=user.organization,
        created_by=user,
        is_default=default,
        is_active=True,
    )


def _another_default_workspace(user, workspace, monkeypatch, auth_client):
    # The DB forbids two simultaneous defaults. Model an in-flight request that
    # holds the old default snapshot while another workspace becomes default.
    # Both project authorization and the log manager still run their real Qs.
    client_module = inspect.getmodule(type(auth_client))

    Workspace.no_workspace_objects.filter(id=workspace.id).update(is_default=False)
    other = _workspace(user, default=True)
    original_initial = client_module._initial_with_workspace

    def initial(view, request, *args, **kwargs):
        result = original_initial(view, request, *args, **kwargs)
        request.workspace.is_default = True
        return result

    monkeypatch.setattr(client_module, "_initial_with_workspace", initial)
    return other


def test_i15_nondefault_workspace_cannot_read_other_workspace(
    auth_client, log_factory, user, project, fake_reader
):
    log = log_factory()
    active, other = _workspace(user), _workspace(user)
    APICallLog.no_workspace_objects.filter(pk=log.pk).update(workspace=active)
    Project.no_workspace_objects.filter(id=project.id).update(workspace=other)
    auth_client.set_workspace(active)
    fake_reader(log)
    _nav(_get(auth_client, log), "unavailable")
    fake_reader.reader.assert_not_called()


@pytest.mark.parametrize(
    "project_workspace", ["null", "another_default"], ids=["i16", "i17"]
)
def test_default_workspace_compatibility(
    auth_client,
    log_factory,
    user,
    workspace,
    project,
    fake_reader,
    project_workspace,
    monkeypatch,
):
    log = log_factory()
    # A null log workspace avoids contradicting the explicit consistency fence.
    APICallLog.no_workspace_objects.filter(pk=log.pk).update(workspace=None)
    target = (
        None
        if project_workspace == "null"
        else _another_default_workspace(user, workspace, monkeypatch, auth_client)
    )
    Project.no_workspace_objects.filter(id=project.id).update(workspace=target)
    fake_reader(log)
    _nav(_get(auth_client, log))


def test_i18_log_project_workspace_consistency(
    auth_client, log_factory, user, workspace, project, fake_reader, monkeypatch
):
    log = log_factory()
    Project.no_workspace_objects.filter(id=project.id).update(
        workspace=_another_default_workspace(user, workspace, monkeypatch, auth_client)
    )
    fake_reader(log)
    _nav(_get(auth_client, log), "invalid_reference")
    fake_reader.reader.assert_not_called()


def test_i19_older_log_after_a_template_swap_is_not_recorded(
    auth_client,
    log_factory,
    collector_tree,
    custom_eval_config,
    eval_template,
):
    log = _older_log(log_factory, collector_tree)
    swapped = EvalTemplate.objects.create(
        name="Swapped template",
        organization=eval_template.organization,
        workspace=eval_template.workspace,
        config=eval_template.config,
    )
    CustomEvalConfig.all_objects.filter(id=custom_eval_config.id).update(
        eval_template=swapped
    )
    _nav(_get(auth_client, log), NOT_RECORDED)


def test_i20_forged_other_org_log(auth_client, log_factory, fake_reader):
    log = log_factory()
    other = Organization.objects.create(name="Other log owner")
    APICallLog.no_workspace_objects.filter(pk=log.pk).update(
        organization=other, workspace=None
    )
    fake_reader(log)
    assert _get(auth_client, log).status_code == 400
    fake_reader.reader.assert_not_called()


def test_i21_ce_early_return(auth_client, monkeypatch):
    monkeypatch.setattr("model_hub.views.separate_evals.APICallLog", None)
    resolver = Mock(side_effect=AssertionError("CE must not resolve"))
    monkeypatch.setattr(
        "model_hub.views.separate_evals.resolve_eval_log_source_navigation", resolver
    )
    response = _get(auth_client, SimpleNamespace(log_id=uuid4()))
    assert response.status_code == 200
    assert response.json()["result"] == []
    resolver.assert_not_called()


def test_i22_outage_preserves_eval(auth_client, log_factory, fake_reader):
    log = log_factory()
    reader = fake_reader(log)
    reader.side_effect = TraceDetailReadUnavailable("clickhouse_query_failed")
    response = _get(auth_client, log)
    assert _nav(response, "temporarily_unavailable")["retryable"] is True
    assert response.json()["result"]["output"] == json.loads(log.config)["output"]


@pytest.mark.parametrize(
    "output",
    [
        {"output": 0.75, "reason": "numeric"},
        {"output": False, "reason": "boolean"},
        {"output": "A", "reason": "categorical"},
        {"error": "stored failure"},
        {"output": [{"name": "score", "value": 1}, {"name": "safe", "value": True}]},
    ],
)
def test_i23_outputs_are_unchanged(auth_client, log_factory, fake_reader, output):
    log = log_factory(extra={"output": output})
    fake_reader(log)
    before = _get(auth_client, log, None).json()["result"]
    after = _get(auth_client, log).json()["result"]
    for key in ("output", "values", "required_keys", "evaluation_id"):
        assert json.dumps(before[key]) == json.dumps(after[key])


def test_i24_resolution_and_retry_create_no_rows(auth_client, log_factory, fake_reader):
    log = log_factory()
    fake_reader(log)
    models = (APICallLog, Feedback, Score)
    before = [model.no_workspace_objects.count() for model in models]
    _nav(_get(auth_client, log))
    _nav(_get(auth_client, log))
    assert [model.no_workspace_objects.count() for model in models] == before


def test_i25_bounded_pg_and_ch_queries(
    auth_client,
    log_factory,
    collector_tree,
    clickhouse_queries,
    django_assert_num_queries,
):
    log = _tree_log(log_factory, collector_tree)
    # Warm URL imports before measuring; the API fixture adds one workspace read.
    _get(auth_client, log, None)
    with django_assert_num_queries(4), CaptureQueriesContext(connection) as pg:
        _nav(_get(auth_client, log))
    # workspace injection + log + localizer + the recorded project's scope
    # check; no eval config read and no organization-wide project list
    assert len(pg) == 4
    assert not any("tracer_custom_eval_config" in q["sql"] for q in pg.captured_queries)
    assert clickhouse_queries.call_count == 2
    assert all(
        "tracer_eval_logger" not in call.args[0]
        and "model_hub_score" not in call.args[0]
        for call in clickhouse_queries.call_args_list
    )


def test_i25b_older_log_reads_no_project_and_no_trace(
    auth_client,
    log_factory,
    collector_tree,
    clickhouse_queries,
    django_assert_num_queries,
):
    log = _older_log(log_factory, collector_tree)
    _get(auth_client, log, None)
    with django_assert_num_queries(3), CaptureQueriesContext(connection) as pg:
        _nav(_get(auth_client, log), NOT_RECORDED)
    # workspace injection + log + localizer only
    assert not any("tracer_project" in q["sql"] for q in pg.captured_queries)
    clickhouse_queries.assert_not_called()


@pytest.mark.parametrize("role", ["viewer", "workspace_viewer"])
def test_i26_read_only_roles_can_open_trace(
    auth_client, log_factory, collector_tree, user, workspace, role
):
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    org_role = (
        OrganizationRoles.MEMBER_VIEW_ONLY
        if role == "viewer"
        else OrganizationRoles.WORKSPACE_VIEWER
    )
    user.organization_role = org_role
    user.save(update_fields=["organization_role"])
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role=org_role, level=Level.VIEWER
    )
    WorkspaceMembership.no_workspace_objects.filter(
        user=user, workspace=workspace
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    nav = _nav(_get(auth_client, _tree_log(log_factory, collector_tree)))
    response = auth_client.get(
        f"/tracer/trace/{nav['trace_id']}/", {"project_id": nav["project_id"]}
    )
    assert response.status_code == 200, response.content


def test_i27_session_target_does_not_read(auth_client, log_factory, fake_reader):
    log = log_factory(
        extra={
            "target_type": "session",
            "session_id": str(uuid4()),
            "trace_id": None,
            "span_id": None,
        }
    )
    fake_reader(log)
    _nav(_get(auth_client, log), "unsupported_target")
    fake_reader.reader.assert_not_called()


def test_view_guard_preserves_evaluation(auth_client, log_factory, monkeypatch):
    log = log_factory()
    monkeypatch.setattr(
        "model_hub.views.separate_evals.resolve_eval_log_source_navigation",
        Mock(side_effect=RuntimeError("unexpected resolver regression")),
    )
    response = _get(auth_client, log)
    _nav(response, "temporarily_unavailable")
    assert response.json()["result"]["output"] == json.loads(log.config)["output"]
