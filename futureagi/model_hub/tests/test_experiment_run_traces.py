"""One experiment run's traces, joined by run id, through the real views.

The span store is replaced by a fake page reader (tracer/tests/test_trace_root_reads.py covers the real one's two
bounded queries, and the stack walk in the e2e suite reads the real ClickHouse); every other part, from run
creation and ingest to the scope checks and the caller's project scope, is the real code.
"""

import uuid

import pytest
from django.urls import reverse
from rest_framework import status

from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset
from model_hub.services import experiment_run_traces as traces_service
from model_hub.services.experiment_run_traces import (
    NO_RUN_ID,
    NOT_FOUND,
    OTHER_RUN,
    RUN_ID_ATTRIBUTE,
    list_run_traces,
)
from tracer.services.clickhouse.v2.trace_root_reads import TraceRoot

pytestmark = pytest.mark.usefixtures("urlconf_loaded_without_workspace_context")


def _dataset(organization, workspace, user, cases):
    dataset = Dataset.no_workspace_objects.create(
        name=f"run-traces-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    version = DatasetVersion.no_workspace_objects.create(dataset=dataset)
    examples = [
        DatasetVersionItem.no_workspace_objects.create(
            dataset_version=version, input={"case": value}, expected={"answer": value}
        )
        for value in range(cases)
    ]
    return dataset, version, examples


def _run(auth_client, version, examples, trace_ids):
    """Create a run and ingest one completed item per example, the i-th carrying trace_ids[i] (None: no trace)."""
    run_id = str(uuid.uuid4())
    plan = [
        {"example_id": str(example.example_id), "trial_number": 1}
        for example in examples
    ]
    created = auth_client.post(
        reverse("external-experiment-run-create"),
        {
            "run_id": run_id,
            "dataset_version_id": str(version.id),
            "expected_items": plan,
            "evaluator_metric_ids": [],
            "model_config": {"provider": "fixture", "model": "deterministic"},
            "metadata": {"code_revision": "fixture-v1"},
        },
        format="json",
    )
    assert created.status_code == status.HTTP_200_OK, created.json()
    items = []
    for index, (identity, trace_id) in enumerate(zip(plan, trace_ids, strict=True)):
        item = {
            **identity,
            "receipt_key": f"{run_id}-{index}",
            "status": "completed",
            "output": {"i": index},
        }
        if trace_id is not None:
            item.update(trace_id=trace_id, root_span_id=f"root-{trace_id}")
        items.append(item)
    ingested = auth_client.post(
        reverse("external-experiment-run-results", kwargs={"run_id": run_id}),
        {"items": items},
        format="json",
    )
    assert ingested.status_code == status.HTTP_200_OK, ingested.json()
    return run_id


def _span(
    trace_id,
    run_id,
    *,
    root_id=None,
    parent=None,
    example="ex",
    trial=1,
    name="experiment.trial",
):
    attrs = {"fi.experiment.example_id": example}
    if run_id is not None:
        attrs[RUN_ID_ATTRIBUTE] = run_id
    return {
        "id": root_id or f"root-{trace_id}",
        "trace_id": trace_id,
        "parent_span_id": parent or "",
        "name": name,
        "start_time": None,
        "attrs_string": attrs,
        "attrs_number": {"fi.experiment.trial": trial},
        "attrs_bool": {},
        "span_attributes": "{}",
        "project_id": PROJECT,
    }


PROJECT = "11111111-1111-1111-1111-111111111111"


class _Store:
    """A fake span store: trace id -> span rows. Its page reader answers the way the real one does (the recorded root
    span, else the earliest parentless span; the live span count) and records each read: the project scope it was
    built with and the (trace id, recorded root id) pairs of each page."""

    def __init__(self, traces):
        self.traces = traces
        self.scopes = []
        self.pages = []

    def reader(self, project_ids):
        self.scopes.append(list(project_ids))

        def read(wanted):
            self.pages.append(list(wanted))
            found = {}
            for trace_id, root_id in wanted:
                spans = self.traces.get(trace_id) or []
                root = next((r for r in spans if root_id and r["id"] == root_id), None)
                root = root or next((r for r in spans if not r["parent_span_id"]), None)
                if root is not None:
                    found[trace_id] = TraceRoot(
                        project_id=root["project_id"], span_count=len(spans), root=root
                    )
            return found

        return read


@pytest.fixture
def store(monkeypatch, organization, workspace):
    """The fake page reader behind the view's real project scope: one Observe project in the caller's workspace."""
    fake = _Store({})
    monkeypatch.setattr(traces_service, "_default_reader", fake.reader)
    _project(organization, workspace, project_id=PROJECT)
    return fake


def _project(organization, workspace, *, project_id=None, name=None):
    from tracer.models.project import Project

    return Project.no_workspace_objects.create(
        **({"id": project_id} if project_id else {}),
        name=name or f"run-traces-{uuid.uuid4().hex[:6]}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def _traces(client, run_id, **query):
    return client.get(
        reverse("experiment-run-traces", kwargs={"run_id": run_id}), query
    )


@pytest.mark.django_db
def test_each_run_lists_only_the_traces_whose_root_names_it(
    auth_client, organization, workspace, user, store
):
    _, version, examples = _dataset(organization, workspace, user, cases=2)
    run_a = _run(auth_client, version, examples, ["t-a1", "t-a2"])
    run_b = _run(auth_client, version, examples, ["t-b1", "t-b2"])
    store.traces.update(
        {
            "t-a1": [
                _span("t-a1", run_a),
                _span("t-a1", run_a, root_id="child", parent="root-t-a1", name="llm"),
            ],
            "t-a2": [_span("t-a2", run_a)],
            "t-b1": [_span("t-b1", run_b)],
            "t-b2": [_span("t-b2", run_b)],
        }
    )

    for run_id, mine, theirs in (
        (run_a, {"t-a1", "t-a2"}, {"t-b1", "t-b2"}),
        (run_b, {"t-b1", "t-b2"}, {"t-a1"}),
    ):
        response = _traces(auth_client, run_id)
        assert response.status_code == status.HTTP_200_OK, response.json()
        result = response.json()["result"]
        listed = {row["trace_id"] for row in result["traces"]}
        assert listed == mine and not listed & theirs
        assert result["run_id"] == run_id and result["not_joined"] == []
        assert result["items_with_trace"] == 2 and result["has_more"] is False
        assert all(row["trace_run_id"] == run_id for row in result["traces"])

    first = next(
        row
        for row in _traces(auth_client, run_a).json()["result"]["traces"]
        if row["trace_id"] == "t-a1"
    )
    assert first["span_count"] == 2 and first["root_span_name"] == "experiment.trial"
    assert first["root_span_id"] == "root-t-a1" and first["trace_trial"] == 1
    # one span-store read per page, with every item's recorded root span id
    assert sorted(store.pages[-1]) == [("t-a1", "root-t-a1"), ("t-a2", "root-t-a2")]
    assert store.scopes and all(scope == [PROJECT] for scope in store.scopes)


@pytest.mark.django_db
def test_a_trace_naming_another_run_or_none_or_missing_is_never_listed(
    auth_client, organization, workspace, user, store
):
    _, version, examples = _dataset(organization, workspace, user, cases=4)
    other = str(uuid.uuid4())
    run_id = _run(
        auth_client, version, examples, ["t-mine", "t-other", "t-none", "t-gone"]
    )
    store.traces.update(
        {
            "t-mine": [_span("t-mine", run_id)],
            "t-other": [_span("t-other", other)],
            "t-none": [_span("t-none", None)],
        }
    )

    result = _traces(auth_client, run_id).json()["result"]
    assert [row["trace_id"] for row in result["traces"]] == ["t-mine"]
    reasons = {
        row["trace_id"]: (row["reason"], row["trace_run_id"])
        for row in result["not_joined"]
    }
    assert reasons == {
        "t-other": (OTHER_RUN, other),
        "t-none": (NO_RUN_ID, None),
        "t-gone": (NOT_FOUND, None),
    }
    assert result["items_with_trace"] == 4


@pytest.mark.django_db
def test_items_without_a_trace_are_not_counted_and_pages_are_bounded(
    auth_client, organization, workspace, user, store
):
    _, version, examples = _dataset(organization, workspace, user, cases=3)
    run_id = _run(auth_client, version, examples, ["t-1", None, "t-3"])
    store.traces.update({"t-1": [_span("t-1", run_id)], "t-3": [_span("t-3", run_id)]})

    page = _traces(auth_client, run_id, limit=1).json()["result"]
    assert (
        page["items_with_trace"] == 2
        and page["limit"] == 1
        and page["has_more"] is True
    )
    assert len(page["traces"]) + len(page["not_joined"]) == 1
    rest = _traces(auth_client, run_id, limit=1, offset=1).json()["result"]
    assert rest["has_more"] is False
    assert {r["trace_id"] for r in page["traces"] + rest["traces"]} == {"t-1", "t-3"}
    assert (
        _traces(auth_client, run_id, limit=0).status_code == status.HTTP_400_BAD_REQUEST
    )
    assert (
        _traces(auth_client, run_id, unknown=1).status_code
        == status.HTTP_400_BAD_REQUEST
    )


@pytest.mark.django_db
def test_an_unknown_run_is_404(auth_client, store):
    assert _traces(auth_client, uuid.uuid4()).status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_a_page_is_one_span_store_read_with_the_recorded_root_ids(
    organization, workspace, user, auth_client
):
    _, version, examples = _dataset(organization, workspace, user, cases=3)
    run_id = _run(auth_client, version, examples, ["t-1", "t-2", "t-3"])
    from model_hub.models.experiments import ExperimentRun

    run = ExperimentRun.no_workspace_objects.get(id=run_id)
    calls = []

    def read(wanted):
        calls.append(list(wanted))
        return {
            trace_id: TraceRoot(
                project_id=PROJECT,
                span_count=1,
                root=_span(trace_id, run_id, root_id=root_id),
            )
            for trace_id, root_id in wanted
        }

    page = list_run_traces(run, project_ids=[PROJECT], read_traces=read)
    assert len(calls) == 1
    assert sorted(calls[0]) == [
        ("t-1", "root-t-1"),
        ("t-2", "root-t-2"),
        ("t-3", "root-t-3"),
    ]
    assert sorted(row["trace_id"] for row in page.traces) == ["t-1", "t-2", "t-3"]
    assert all(row["project_id"] == PROJECT for row in page.traces)


@pytest.mark.django_db
def test_no_project_in_scope_lists_nothing(organization, workspace, user, auth_client):
    _, version, examples = _dataset(organization, workspace, user, cases=1)
    run_id = _run(auth_client, version, examples, ["t-y"])
    from model_hub.models.experiments import ExperimentRun

    run = ExperimentRun.no_workspace_objects.get(id=run_id)
    page = list_run_traces(run, project_ids=[])
    assert page.traces == [] and [row["reason"] for row in page.not_joined] == [
        NOT_FOUND
    ]


@pytest.mark.django_db
def test_a_recorded_otel_trace_id_is_read_in_the_span_stores_dashed_form(
    auth_client, organization, workspace, user, store
):
    # The SDK records the OpenTelemetry id (32 hex digits); fi-collector stores
    # it as a dashed UUID string, which the trace view and its links use.
    _, version, examples = _dataset(organization, workspace, user, cases=1)
    recorded = "83163300c700adc0821b1d5d20fb5073"
    stored = "83163300-c700-adc0-821b-1d5d20fb5073"
    run_id = _run(auth_client, version, examples, [recorded])
    store.traces.update({stored: [_span(stored, run_id, root_id=f"root-{recorded}")]})

    result = _traces(auth_client, run_id).json()["result"]
    assert [row["trace_id"] for row in result["traces"]] == [stored]
    assert result["not_joined"] == []
    assert traces_service.store_trace_id(stored) == stored
    assert traces_service.store_trace_id("t-a1") == "t-a1"


@pytest.mark.django_db
def test_the_span_store_is_read_in_the_callers_projects_only(
    auth_client, organization, workspace, user, store
):
    """The view's real project scope (no patched scope): the caller's Observe projects, never another
    organization's, another workspace's or a deleted one."""
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

    other_org = Organization.objects.create(name="other-traces-org")
    other_ws = Workspace.no_workspace_objects.create(
        name="other-traces-workspace",
        organization=organization,
        created_by=user,
        is_default=False,
    )
    second = _project(organization, workspace)
    _project(other_org, None)
    _project(organization, other_ws)
    deleted = _project(organization, workspace)
    deleted.deleted = True
    deleted.save(update_fields=["deleted"])
    _, version, examples = _dataset(organization, workspace, user, cases=1)
    run_id = _run(auth_client, version, examples, ["t-scope"])
    store.traces["t-scope"] = [_span("t-scope", run_id)]

    response = _traces(auth_client, run_id)
    assert response.status_code == status.HTTP_200_OK, response.json()
    assert [row["trace_id"] for row in response.json()["result"]["traces"]] == [
        "t-scope"
    ]
    assert sorted(store.scopes[-1]) == sorted([PROJECT, str(second.id)])


@pytest.mark.django_db
@pytest.mark.parametrize("hidden_by", ["organization", "workspace", "deleted"])
def test_a_run_outside_the_caller_scope_is_404_and_reads_no_span(
    auth_client, organization, workspace, user, store, hidden_by
):
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace
    from model_hub.models.experiments import ExperimentRun

    dataset, version, examples = _dataset(organization, workspace, user, cases=1)
    run_id = _run(auth_client, version, examples, ["t-hidden"])
    store.traces["t-hidden"] = [_span("t-hidden", run_id)]
    assert _traces(auth_client, run_id).status_code == status.HTTP_200_OK
    store.scopes.clear()
    store.pages.clear()
    if hidden_by == "organization":
        other = Organization.objects.create(name="other-traces-run-org")
        Dataset.no_workspace_objects.filter(id=dataset.id).update(organization=other)
    elif hidden_by == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="other-traces-run-workspace",
            organization=organization,
            created_by=user,
            is_default=False,
        )
        Dataset.no_workspace_objects.filter(id=dataset.id).update(workspace=other)
    else:
        ExperimentRun.no_workspace_objects.filter(id=run_id).update(deleted=True)

    response = _traces(auth_client, run_id)
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert "t-hidden" not in str(response.content)
    assert store.scopes == [] and store.pages == []
